package gateway

import (
	"encoding/hex"
	"errors"
	"net/http"
	"strconv"

	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/policy"
)

func (s *Server) handleAuditList(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActAuditRead, policy.Resource{}) {
		return
	}
	limit := 50
	if v, err := strconv.Atoi(r.URL.Query().Get("limit")); err == nil && v > 0 && v <= 500 {
		limit = v
	}
	before := int64(1<<62 - 1)
	if v, err := strconv.ParseInt(r.URL.Query().Get("before_seq"), 10, 64); err == nil && v > 0 {
		before = v
	}
	rows, err := s.pool.Query(r.Context(), `SELECT seq, event_id, occurred_at, actor, action, target, before_hash,
		after_hash, ip, device, correlation_id, details_json, hash FROM audit.events WHERE seq < $1 ORDER BY seq DESC LIMIT $2`, before, limit)
	if err != nil {
		s.internalError(w, r, "audit list", err)
		return
	}
	defer rows.Close()
	events := []map[string]any{}
	for rows.Next() {
		var seq, occurred int64
		var id, actor, action, target, bh, ah, ip, device, corr, details string
		var hash []byte
		if err := rows.Scan(&seq, &id, &occurred, &actor, &action, &target, &bh, &ah, &ip, &device, &corr, &details, &hash); err != nil {
			s.internalError(w, r, "audit scan", err)
			return
		}
		events = append(events, map[string]any{"seq": seq, "event_id": id, "occurred_at": occurred, "actor": actor,
			"action": action, "target": target, "before_hash": bh, "after_hash": ah, "ip": ip, "device": device,
			"correlation_id": corr, "details_json": details, "hash": hex.EncodeToString(hash)})
	}
	if err := rows.Err(); err != nil {
		s.internalError(w, r, "audit rows", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"events": events})
}

func (s *Server) handleAuditVerify(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActAuditRead, policy.Resource{}) {
		return
	}
	res, err := audit.Verify(r.Context(), s.pool)
	if err != nil && !errors.Is(err, audit.ErrChainBroken) {
		s.internalError(w, r, "audit verify", err)
		return
	}
	if errors.Is(err, audit.ErrChainBroken) {
		s.securityAlert(r.Context(), "audit chain mismatch", "Audit hash chain verification failed.", "audit-chain")
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": err == nil, "count": res.Count, "head_seq": res.HeadSeq,
		"head_hash": res.HeadHash, "broken_at": res.BrokenAt, "reason": res.Reason})
}
