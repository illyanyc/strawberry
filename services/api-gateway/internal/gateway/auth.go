package gateway

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"github.com/go-webauthn/webauthn/protocol"
	"github.com/go-webauthn/webauthn/webauthn"
	"github.com/oklog/ulid/v2"
	"github.com/pquerna/otp"
	"github.com/pquerna/otp/totp"

	"github.com/illyanyc/strawberry/libs/go/policy"
)

const (
	ceremonyTTL       = 5 * time.Minute
	setupTokenTTL     = 30 * time.Minute
	recoveryCodeCount = 10
	totpPeriod        = 30
)

// ---- state ----

func (s *Server) handleState(w http.ResponseWriter, r *http.Request, _ *reqCtx) {
	exists, err := s.store.ownerExists(r.Context())
	if err != nil {
		s.internalError(w, r, "owner lookup", err)
		return
	}
	_, sessErr := s.sessionFromRequest(r)
	writeJSON(w, http.StatusOK, map[string]any{
		"setup_required": !exists,
		"authenticated":  sessErr == nil,
		"rp_id":          s.cfg.RPID,
	})
}

// ---- first-run setup (SE-007) ----

// IssueSetupToken creates a one-time setup token (used by the `setup-token` CLI subcommand). It
// refuses once an owner exists.
func (s *Server) IssueSetupToken(ctx context.Context) (string, error) {
	exists, err := s.store.ownerExists(ctx)
	if err != nil {
		return "", err
	}
	if exists {
		return "", errors.New("an owner already exists; setup is closed")
	}
	tok, err := randomToken(24)
	if err != nil {
		return "", err
	}
	if err := s.store.insertSetupToken(ctx, s.keys.codeHash(tok), setupTokenTTL); err != nil {
		return "", err
	}
	return tok, nil
}

type setupBeginReq struct {
	SetupToken  string `json:"setup_token"`
	DisplayName string `json:"display_name"`
}

func (s *Server) handleSetupBegin(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req setupBeginReq
	if err := decode(r, &req); err != nil || req.SetupToken == "" {
		writeError(w, http.StatusBadRequest, "bad_request", "setup_token required")
		return
	}
	ctx := r.Context()
	if locked := s.checkLock(w, r, rc); locked {
		return
	}
	if exists, err := s.store.ownerExists(ctx); err != nil || exists {
		writeError(w, http.StatusConflict, "setup_closed", "setup is closed")
		return
	}
	ok, err := s.store.setupTokenValid(ctx, s.keys.codeHash(req.SetupToken))
	if err != nil {
		s.internalError(w, r, "setup token", err)
		return
	}
	if !ok {
		s.authFailed(ctx, rc, "setup", "invalid setup token")
		writeError(w, http.StatusUnauthorized, "invalid_setup_token", "invalid or expired setup token")
		return
	}
	handle := make([]byte, 64)
	if _, err := rand.Read(handle); err != nil {
		s.internalError(w, r, "user handle", err)
		return
	}
	o := &Owner{ID: s.cfg.OwnerID, DisplayName: displayName(req.DisplayName), UserHandle: handle}
	creation, data, err := s.wa.BeginRegistration(o)
	if err != nil {
		s.internalError(w, r, "begin registration", err)
		return
	}
	id := newID()
	if err := s.store.saveCeremony(ctx, id, "setup", "", nil, data, ceremonyTTL); err != nil {
		s.internalError(w, r, "save ceremony", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ceremony_id": id, "options": creation.Response})
}

type setupFinishReq struct {
	SetupToken  string          `json:"setup_token"`
	CeremonyID  string          `json:"ceremony_id"`
	DisplayName string          `json:"display_name"`
	PasskeyName string          `json:"passkey_name"`
	Credential  json.RawMessage `json:"credential"`
}

func (s *Server) handleSetupFinish(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req setupFinishReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	ctx := r.Context()
	data, err := s.store.takeCeremony(ctx, req.CeremonyID, "setup", nil)
	if err != nil {
		writeError(w, http.StatusBadRequest, "ceremony_expired", "setup ceremony expired; start again")
		return
	}
	o := &Owner{ID: s.cfg.OwnerID, DisplayName: displayName(req.DisplayName), UserHandle: data.UserID}
	cred, err := s.createCredential(o, data, req.Credential)
	if err != nil {
		s.authFailed(ctx, rc, "setup", "passkey verification failed")
		writeError(w, http.StatusBadRequest, "passkey_invalid", "passkey verification failed")
		return
	}
	consumed, err := s.store.consumeSetupToken(ctx, s.keys.codeHash(req.SetupToken))
	if err != nil || !consumed {
		writeError(w, http.StatusUnauthorized, "invalid_setup_token", "invalid or expired setup token")
		return
	}
	codes, hashes, err := s.newRecoveryCodes()
	if err != nil {
		s.internalError(w, r, "recovery codes", err)
		return
	}
	if err := s.store.createOwner(ctx, o, cred, passkeyName(req.PasskeyName), hashes); err != nil {
		writeError(w, http.StatusConflict, "setup_closed", "setup is closed")
		return
	}
	sess, err := s.startSession(ctx, w, rc, o.ID, "passkey", policy.StepUpPasskey)
	if err != nil {
		s.internalError(w, r, "session", err)
		return
	}
	if err := s.record(ctx, rc, "owner", "auth.setup.complete", o.ID, map[string]any{"passkey": passkeyName(req.PasskeyName), "session": sess.PublicID}, "", ""); err != nil {
		_ = s.store.revokeSessionByHash(ctx, sess.IDHash)
		s.clearSessionCookie(w)
		writeError(w, http.StatusInternalServerError, "audit_unavailable", "audit log unavailable")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"recovery_codes": codes})
}

// ---- passkey login ----

func (s *Server) handleLoginBegin(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if s.checkLock(w, r, rc) {
		return
	}
	assertion, data, err := s.wa.BeginDiscoverableLogin(webauthn.WithUserVerification(protocol.VerificationRequired))
	if err != nil {
		s.internalError(w, r, "begin login", err)
		return
	}
	id := newID()
	if err := s.store.saveCeremony(r.Context(), id, "login", "", nil, data, ceremonyTTL); err != nil {
		s.internalError(w, r, "save ceremony", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ceremony_id": id, "options": assertion.Response})
}

type ceremonyFinishReq struct {
	CeremonyID string          `json:"ceremony_id"`
	Name       string          `json:"name,omitempty"`
	Credential json.RawMessage `json:"credential"`
}

func (s *Server) handleLoginFinish(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req ceremonyFinishReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	ctx := r.Context()
	if s.checkLock(w, r, rc) {
		return
	}
	data, err := s.store.takeCeremony(ctx, req.CeremonyID, "login", nil)
	if err != nil {
		writeError(w, http.StatusBadRequest, "ceremony_expired", "login ceremony expired; start again")
		return
	}
	parsed, err := protocol.ParseCredentialRequestResponseBody(bytes.NewReader(req.Credential))
	if err != nil {
		s.authFailed(ctx, rc, "passkey", "malformed assertion")
		writeError(w, http.StatusUnauthorized, "login_failed", "login failed")
		return
	}
	var owner *Owner
	_, cred, err := s.wa.ValidatePasskeyLogin(func(_, handle []byte) (webauthn.User, error) {
		o, err := s.store.ownerByHandle(ctx, handle)
		owner = o
		return o, err
	}, *data, parsed)
	if err != nil || owner == nil {
		s.authFailed(ctx, rc, "passkey", "assertion rejected")
		writeError(w, http.StatusUnauthorized, "login_failed", "login failed")
		return
	}
	if err := s.store.touchCredential(ctx, cred); err != nil {
		s.internalError(w, r, "touch credential", err)
		return
	}
	s.loginSucceeded(w, r, rc, owner.ID, "passkey", policy.StepUpPasskey)
}

// ---- TOTP login (fallback, reduced assurance: SE-014) ----

type codeReq struct {
	Code string `json:"code"`
}

func (s *Server) handleTOTPLogin(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req codeReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if s.checkLock(w, r, rc) {
		return
	}
	ok, err := s.verifyTOTP(r.Context(), s.cfg.OwnerID, req.Code, false)
	if err != nil {
		s.internalError(w, r, "totp verify", err)
		return
	}
	if !ok {
		s.authFailed(r.Context(), rc, "totp", "invalid code")
		writeError(w, http.StatusUnauthorized, "login_failed", "login failed")
		return
	}
	s.loginSucceeded(w, r, rc, s.cfg.OwnerID, "totp", policy.StepUpTOTP)
}

// ---- recovery-code login (SE-009, SE-014) ----

func (s *Server) handleRecoveryLogin(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req codeReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if s.checkLock(w, r, rc) {
		return
	}
	ok, err := s.store.consumeRecoveryCode(r.Context(), s.cfg.OwnerID, s.keys.codeHash(req.Code))
	if err != nil {
		s.internalError(w, r, "recovery code", err)
		return
	}
	if !ok {
		s.authFailed(r.Context(), rc, "recovery", "invalid recovery code")
		writeError(w, http.StatusUnauthorized, "login_failed", "login failed")
		return
	}
	s.securityAlert(r.Context(), "recovery code used", "A recovery code was used to log in.", "recovery-login")
	s.loginSucceeded(w, r, rc, s.cfg.OwnerID, "recovery", policy.StepUpRecovery)
}

func (s *Server) loginSucceeded(w http.ResponseWriter, r *http.Request, rc *reqCtx, ownerID, method string, stepUp policy.StepUpMethod) {
	ctx := r.Context()
	_ = s.store.clearFailures(ctx, "account:"+ownerID)
	_ = s.store.clearFailures(ctx, "ip:"+rc.ip)
	sess, err := s.startSession(ctx, w, rc, ownerID, method, stepUp)
	if err != nil {
		s.internalError(w, r, "session", err)
		return
	}
	if err := s.record(ctx, rc, "owner", "auth.login.success", ownerID, map[string]any{"method": method, "session": sess.PublicID}, "", ""); err != nil {
		_ = s.store.revokeSessionByHash(ctx, sess.IDHash)
		s.clearSessionCookie(w)
		writeError(w, http.StatusInternalServerError, "audit_unavailable", "audit log unavailable")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"method": method, "reduced_assurance": method != "passkey"})
}

// checkLock writes 429 and returns true when the account or IP is locked out (SE-011).
func (s *Server) checkLock(w http.ResponseWriter, r *http.Request, rc *reqCtx) bool {
	until, err := s.store.lockedUntil(r.Context(), "account:"+s.cfg.OwnerID, "ip:"+rc.ip)
	if err != nil {
		s.internalError(w, r, "lockout check", err)
		return true
	}
	if !until.IsZero() {
		w.Header().Set("Retry-After", until.UTC().Format(http.TimeFormat))
		writeError(w, http.StatusTooManyRequests, "locked_out", "too many failed attempts; try again later")
		return true
	}
	return false
}

// authFailed audits a failed authentication and applies lockout counters (SE-011, SE-023).
func (s *Server) authFailed(ctx context.Context, rc *reqCtx, method, reason string) {
	actor := "anonymous"
	if rc.session != nil {
		actor = "owner"
	}
	_ = s.record(ctx, rc, actor, "auth."+method+".failure", s.cfg.OwnerID, map[string]any{"reason": reason}, "", "")
	for _, key := range []string{"account:" + s.cfg.OwnerID, "ip:" + rc.ip} {
		locked, err := s.store.recordFailure(ctx, key, s.cfg.MaxFailures, s.cfg.LockoutWindow)
		if err != nil {
			s.log.ErrorContext(ctx, "record auth failure", "error", err)
			continue
		}
		if locked {
			_ = s.record(ctx, rc, actor, "auth.lockout", key, map[string]any{"method": method}, "", "")
			s.securityAlert(ctx, "auth lockout", "Authentication locked after repeated failures ("+keyKind(key)+").", "lockout-"+key)
		}
	}
	s.securityAlert(ctx, "auth failure", "Failed "+method+" authentication.", "auth-failure-"+method)
}

func keyKind(key string) string { return strings.SplitN(key, ":", 2)[0] }

// ---- session info, logout, sessions ----

type credentialView struct {
	ID         string     `json:"id"`
	Name       string     `json:"name"`
	CreatedAt  time.Time  `json:"created_at"`
	LastUsedAt *time.Time `json:"last_used_at"`
}

func (s *Server) handleMe(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	ctx := r.Context()
	o, err := s.store.owner(ctx, rc.session.OwnerID)
	if err != nil {
		s.internalError(w, r, "owner", err)
		return
	}
	creds := make([]credentialView, 0, len(o.Credentials))
	for _, c := range o.Credentials {
		creds = append(creds, credentialView{ID: base64.RawURLEncoding.EncodeToString(c.Credential.ID), Name: c.Name, CreatedAt: c.CreatedAt, LastUsedAt: c.LastUsedAt})
	}
	t, err := s.store.totp(ctx, o.ID)
	totpOn := err == nil && t.Confirmed
	left, _ := s.store.recoveryCodesLeft(ctx, o.ID)
	writeJSON(w, http.StatusOK, map[string]any{
		"owner":               map[string]string{"id": o.ID, "display_name": o.DisplayName},
		"session":             map[string]any{"id": rc.session.PublicID, "auth_method": rc.session.AuthMethod, "step_up_method": rc.session.StepUpMethod, "step_up_until": rc.session.StepUpUntil, "idle_expires_at": rc.session.IdleExpires, "absolute_expires_at": rc.session.AbsExpires},
		"passkeys":            creds,
		"totp_enabled":        totpOn,
		"recovery_codes_left": left,
	})
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	ctx := r.Context()
	if err := s.store.revokeSessionByHash(ctx, rc.session.IDHash); err != nil {
		s.internalError(w, r, "logout", err)
		return
	}
	s.clearSessionCookie(w)
	_ = s.record(ctx, rc, "owner", "auth.logout", rc.session.OwnerID, map[string]any{"session": rc.session.PublicID}, "", "")
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleListSessions(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActAuthSessionRead, policy.Resource{}) {
		return
	}
	list, err := s.store.listSessions(r.Context(), rc.session.OwnerID)
	if err != nil {
		s.internalError(w, r, "sessions", err)
		return
	}
	out := make([]map[string]any, 0, len(list))
	for _, x := range list {
		out = append(out, map[string]any{"id": x.PublicID, "auth_method": x.AuthMethod, "created_at": x.CreatedAt,
			"last_seen_at": x.LastSeenAt, "ip": x.IP, "user_agent": x.UserAgent, "current": x.PublicID == rc.session.PublicID})
	}
	writeJSON(w, http.StatusOK, map[string]any{"sessions": out})
}

func (s *Server) handleRevokeSession(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActAuthSessionRevoke, policy.Resource{}) {
		return
	}
	id := r.PathValue("id")
	ok, err := s.store.revokeSession(r.Context(), rc.session.OwnerID, id)
	if err != nil {
		s.internalError(w, r, "revoke", err)
		return
	}
	if !ok {
		writeError(w, http.StatusNotFound, "not_found", "session not found")
		return
	}
	if err := s.record(r.Context(), rc, "owner", "auth.session.revoke", id, nil, "", ""); err != nil {
		writeError(w, http.StatusInternalServerError, "audit_unavailable", "audit log unavailable")
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

// ---- passkey management ----

func (s *Server) handleRegisterBegin(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActAuthPasskeyRegister, policy.Resource{}) {
		return
	}
	ctx := r.Context()
	o, err := s.store.owner(ctx, rc.session.OwnerID)
	if err != nil {
		s.internalError(w, r, "owner", err)
		return
	}
	excl := make([]protocol.CredentialDescriptor, 0, len(o.Credentials))
	for _, c := range o.Credentials {
		excl = append(excl, c.Credential.Descriptor())
	}
	creation, data, err := s.wa.BeginRegistration(o, webauthn.WithExclusions(excl))
	if err != nil {
		s.internalError(w, r, "begin registration", err)
		return
	}
	id := newID()
	if err := s.store.saveCeremony(ctx, id, "register", o.ID, rc.session.IDHash, data, ceremonyTTL); err != nil {
		s.internalError(w, r, "save ceremony", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ceremony_id": id, "options": creation.Response})
}

func (s *Server) handleRegisterFinish(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActAuthPasskeyRegister, policy.Resource{}) {
		return
	}
	var req ceremonyFinishReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	ctx := r.Context()
	data, err := s.store.takeCeremony(ctx, req.CeremonyID, "register", rc.session.IDHash)
	if err != nil {
		writeError(w, http.StatusBadRequest, "ceremony_expired", "registration ceremony expired; start again")
		return
	}
	o, err := s.store.owner(ctx, rc.session.OwnerID)
	if err != nil {
		s.internalError(w, r, "owner", err)
		return
	}
	cred, err := s.createCredential(o, data, req.Credential)
	if err != nil {
		writeError(w, http.StatusBadRequest, "passkey_invalid", "passkey verification failed")
		return
	}
	name := passkeyName(req.Name)
	if err := s.record(ctx, rc, "owner", "auth.passkey.register", o.ID, map[string]any{"name": name}, "", ""); err != nil {
		writeError(w, http.StatusInternalServerError, "audit_unavailable", "audit log unavailable")
		return
	}
	if err := s.store.addCredential(ctx, o.ID, cred, name); err != nil {
		s.internalError(w, r, "add credential", err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]string{"id": base64.RawURLEncoding.EncodeToString(cred.ID), "name": name})
}

func (s *Server) handleDeleteCredential(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActAuthPasskeyDelete, policy.Resource{}) {
		return
	}
	id, err := base64.RawURLEncoding.DecodeString(r.PathValue("id"))
	if err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", "invalid credential id")
		return
	}
	ctx := r.Context()
	if err := s.record(ctx, rc, "owner", "auth.passkey.delete", r.PathValue("id"), nil, "", ""); err != nil {
		writeError(w, http.StatusInternalServerError, "audit_unavailable", "audit log unavailable")
		return
	}
	switch err := s.store.deleteCredential(ctx, rc.session.OwnerID, id); {
	case errors.Is(err, errLastPasskey):
		writeError(w, http.StatusConflict, "last_passkey", "cannot delete the last passkey")
	case errors.Is(err, errNotFound):
		writeError(w, http.StatusNotFound, "not_found", "passkey not found")
	case err != nil:
		s.internalError(w, r, "delete credential", err)
	default:
		w.WriteHeader(http.StatusNoContent)
	}
}

// ---- TOTP enrollment (SE-008) ----

func (s *Server) handleTOTPEnroll(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActAuthTOTPEnroll, policy.Resource{}) {
		return
	}
	key, err := totp.Generate(totp.GenerateOpts{Issuer: s.cfg.RPDisplayName, AccountName: rc.session.OwnerID, Period: totpPeriod, Digits: otp.DigitsSix, Algorithm: otp.AlgorithmSHA1})
	if err != nil {
		s.internalError(w, r, "totp generate", err)
		return
	}
	enc, err := s.keys.sealTOTP([]byte(key.Secret()), rc.session.OwnerID)
	if err != nil {
		s.internalError(w, r, "totp seal", err)
		return
	}
	if err := s.store.putPendingTOTP(r.Context(), rc.session.OwnerID, enc); err != nil {
		s.internalError(w, r, "totp store", err)
		return
	}
	// The secret goes to the owner exactly once, to be scanned into an authenticator app.
	writeJSON(w, http.StatusOK, map[string]string{"otpauth_url": key.URL(), "secret": key.Secret()})
}

func (s *Server) handleTOTPVerify(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActAuthTOTPEnroll, policy.Resource{}) {
		return
	}
	var req codeReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	ok, err := s.verifyTOTP(r.Context(), rc.session.OwnerID, req.Code, true)
	if err != nil {
		s.internalError(w, r, "totp verify", err)
		return
	}
	if !ok {
		writeError(w, http.StatusBadRequest, "invalid_code", "code did not match")
		return
	}
	if err := s.record(r.Context(), rc, "owner", "auth.totp.enroll", rc.session.OwnerID, nil, "", ""); err != nil {
		writeError(w, http.StatusInternalServerError, "audit_unavailable", "audit log unavailable")
		return
	}
	writeJSON(w, http.StatusOK, map[string]bool{"totp_enabled": true})
}

// verifyTOTP checks a 6-digit code within ±1 step and records its counter so it cannot be replayed.
// With confirm=false the enrollment must already be confirmed; confirm=true accepts a pending secret.
func (s *Server) verifyTOTP(ctx context.Context, ownerID, code string, confirm bool) (bool, error) {
	code = strings.TrimSpace(code)
	if len(code) != 6 {
		return false, nil
	}
	row, err := s.store.totp(ctx, ownerID)
	if errors.Is(err, errNotFound) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	if !confirm && !row.Confirmed {
		return false, nil
	}
	secret, err := s.keys.openTOTP(row.SecretEnc, ownerID)
	if err != nil {
		return false, err
	}
	now := s.now().Unix() / totpPeriod
	for skew := int64(-1); skew <= 1; skew++ {
		counter := now + skew
		want, err := totp.GenerateCodeCustom(string(secret), time.Unix(counter*totpPeriod, 0), totp.ValidateOpts{
			Period: totpPeriod, Digits: otp.DigitsSix, Algorithm: otp.AlgorithmSHA1,
		})
		if err != nil {
			return false, err
		}
		if subtle.ConstantTimeCompare([]byte(want), []byte(code)) == 1 {
			return s.store.advanceTOTP(ctx, ownerID, counter, confirm)
		}
	}
	return false, nil
}

// ---- recovery codes (SE-009) ----

func (s *Server) newRecoveryCodes() ([]string, [][]byte, error) {
	codes := make([]string, recoveryCodeCount)
	hashes := make([][]byte, recoveryCodeCount)
	for i := range codes {
		c, err := newRecoveryCode()
		if err != nil {
			return nil, nil, err
		}
		codes[i], hashes[i] = c, s.keys.codeHash(c)
	}
	return codes, hashes, nil
}

func (s *Server) handleRecoveryRegenerate(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActAuthRecoveryRegen, policy.Resource{}) {
		return
	}
	codes, hashes, err := s.newRecoveryCodes()
	if err != nil {
		s.internalError(w, r, "recovery codes", err)
		return
	}
	if err := s.record(r.Context(), rc, "owner", "auth.recovery.regenerate", rc.session.OwnerID, nil, "", ""); err != nil {
		writeError(w, http.StatusInternalServerError, "audit_unavailable", "audit log unavailable")
		return
	}
	if err := s.store.replaceRecoveryCodes(r.Context(), rc.session.OwnerID, hashes); err != nil {
		s.internalError(w, r, "recovery store", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"recovery_codes": codes})
}

// ---- step-up (SE-003) ----

func (s *Server) handleStepUpBegin(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	ctx := r.Context()
	o, err := s.store.owner(ctx, rc.session.OwnerID)
	if err != nil {
		s.internalError(w, r, "owner", err)
		return
	}
	assertion, data, err := s.wa.BeginLogin(o, webauthn.WithUserVerification(protocol.VerificationRequired))
	if err != nil {
		s.internalError(w, r, "begin step-up", err)
		return
	}
	id := newID()
	if err := s.store.saveCeremony(ctx, id, "step_up", o.ID, rc.session.IDHash, data, ceremonyTTL); err != nil {
		s.internalError(w, r, "save ceremony", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ceremony_id": id, "options": assertion.Response})
}

func (s *Server) handleStepUpFinish(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req ceremonyFinishReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	ctx := r.Context()
	data, err := s.store.takeCeremony(ctx, req.CeremonyID, "step_up", rc.session.IDHash)
	if err != nil {
		writeError(w, http.StatusBadRequest, "ceremony_expired", "step-up ceremony expired; start again")
		return
	}
	o, err := s.store.owner(ctx, rc.session.OwnerID)
	if err != nil {
		s.internalError(w, r, "owner", err)
		return
	}
	parsed, err := protocol.ParseCredentialRequestResponseBody(bytes.NewReader(req.Credential))
	var cred *webauthn.Credential
	if err == nil {
		cred, err = s.wa.ValidateLogin(o, *data, parsed)
	}
	if err != nil {
		s.authFailed(ctx, rc, "step_up", "passkey assertion rejected")
		writeError(w, http.StatusUnauthorized, "step_up_failed", "step-up failed")
		return
	}
	_ = s.store.touchCredential(ctx, cred)
	s.stepUpSucceeded(w, r, rc, policy.StepUpPasskey)
}

func (s *Server) handleStepUpTOTP(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	var req codeReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	if s.checkLock(w, r, rc) {
		return
	}
	ok, err := s.verifyTOTP(r.Context(), rc.session.OwnerID, req.Code, false)
	if err != nil {
		s.internalError(w, r, "totp verify", err)
		return
	}
	if !ok {
		s.authFailed(r.Context(), rc, "step_up", "invalid TOTP code")
		writeError(w, http.StatusUnauthorized, "step_up_failed", "step-up failed")
		return
	}
	s.stepUpSucceeded(w, r, rc, policy.StepUpTOTP)
}

func (s *Server) stepUpSucceeded(w http.ResponseWriter, r *http.Request, rc *reqCtx, method policy.StepUpMethod) {
	until := s.now().Add(s.cfg.StepUpWindow)
	if err := s.record(r.Context(), rc, "owner", "auth.step_up.success", rc.session.OwnerID, map[string]any{"method": string(method)}, "", ""); err != nil {
		writeError(w, http.StatusInternalServerError, "audit_unavailable", "audit log unavailable")
		return
	}
	if err := s.store.setStepUp(r.Context(), rc.session.IDHash, string(method), until); err != nil {
		s.internalError(w, r, "step-up", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"method": method, "step_up_until": until})
}

// ---- helpers ----

func (s *Server) createCredential(o *Owner, data *webauthn.SessionData, raw json.RawMessage) (*webauthn.Credential, error) {
	parsed, err := protocol.ParseCredentialCreationResponseBody(bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	return s.wa.CreateCredential(o, *data, parsed)
}

func newID() string { return ulid.MustNew(ulid.Now(), rand.Reader).String() }

func displayName(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return "Owner"
	}
	return truncate(s, 64)
}

func passkeyName(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return "Passkey"
	}
	return truncate(s, 64)
}
