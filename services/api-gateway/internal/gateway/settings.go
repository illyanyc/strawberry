package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"sync"
	"time"

	"github.com/illyanyc/strawberry/libs/go/audit"
	"github.com/illyanyc/strawberry/libs/go/policy"
)

// settingSpec describes a known platform setting. Unknown keys are rejected (closed list).
type settingSpec struct {
	kind string // "bool" | "enum"
	enum []string
	def  any
	// enableAction, when set, is the action required to set a bool to true (e.g. live trading).
	enableAction policy.Action
}

// settingSpecs is the closed list of UI-editable settings (SE-025). Dangerous enables map to step-up actions.
var settingSpecs = map[string]settingSpec{
	"ui.theme":                      {kind: "enum", enum: []string{"dark", "light"}, def: "dark"},
	"trading.live_enabled":          {kind: "bool", def: false, enableAction: policy.ActTradingLiveEnable},
	"mobile.order_entry_enabled":    {kind: "bool", def: false, enableAction: policy.ActMobileOrderEnable},
	"assistant.write_tools_enabled": {kind: "bool", def: false, enableAction: policy.ActAssistantWriteEnable},
	"alerts.push_fills_enabled":     {kind: "bool", def: false},
	// SE-065: TradingView's embeddable widget needs frame-src https://s.tradingview.com in the SPA's CSP.
	"ui.tradingview_widget_enabled": {kind: "bool", def: false, enableAction: policy.ActUITradingViewWidget},
}

func (s *Server) handleSettingsGet(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	if !s.authorize(w, rc, policy.ActSettingsRead, policy.Resource{}) {
		return
	}
	stored, err := s.store.settings(r.Context(), rc.session.OwnerID)
	if err != nil {
		s.internalError(w, r, "settings", err)
		return
	}
	out := map[string]any{}
	for key, spec := range settingSpecs {
		item := map[string]any{"value": spec.def, "version": 0, "default": true}
		if row, ok := stored[key]; ok {
			var v any
			_ = json.Unmarshal(row.Value, &v)
			item = map[string]any{"value": v, "version": row.Version, "default": false, "updated_at": row.UpdatedAt, "updated_by": row.UpdatedBy}
		}
		out[key] = item
	}
	writeJSON(w, http.StatusOK, map[string]any{"settings": out})
}

type settingPutReq struct {
	Value   json.RawMessage `json:"value"`
	Version int             `json:"version"`
}

func (s *Server) handleSettingsPut(w http.ResponseWriter, r *http.Request, rc *reqCtx) {
	key := r.PathValue("key")
	spec, ok := settingSpecs[key]
	if !ok {
		writeError(w, http.StatusNotFound, "unknown_setting", "unknown setting")
		return
	}
	var req settingPutReq
	if err := decode(r, &req); err != nil {
		writeError(w, http.StatusBadRequest, "bad_request", err.Error())
		return
	}
	val, err := validateSetting(spec, req.Value)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_value", err.Error())
		return
	}
	action := policy.ActSettingsWrite
	if b, isBool := val.(bool); isBool && b && spec.enableAction != "" {
		action = spec.enableAction
	}
	if !s.authorize(w, rc, action, policy.Resource{ID: key}) {
		return
	}
	ctx := r.Context()
	stored, err := s.store.settings(ctx, rc.session.OwnerID)
	if err != nil {
		s.internalError(w, r, "settings", err)
		return
	}
	before := spec.def
	if row, ok := stored[key]; ok {
		_ = json.Unmarshal(row.Value, &before)
	}
	raw, _ := json.Marshal(val)
	ev := audit.Event{Actor: "owner", Action: "settings.update", Target: key, IP: rc.ip, Device: rc.ua,
		BeforeHash: audit.StateHash(before), AfterHash: audit.StateHash(val), Details: map[string]any{"action": string(action)}}
	version, err := s.store.putSetting(ctx, rc.session.OwnerID, key, raw, req.Version, "owner", ev)
	if errors.Is(err, errConflict) {
		writeError(w, http.StatusConflict, "version_conflict", "setting changed since it was read; reload")
		return
	}
	if err != nil {
		s.internalError(w, r, "put setting", err)
		return
	}
	s.flags.invalidate()
	writeJSON(w, http.StatusOK, map[string]any{"key": key, "value": val, "version": version})
}

// flagCache caches the two settings the gateway itself reads on hot paths (the widget CSP and fill
// notifications) for a few seconds; a settings write invalidates it.
type flagCache struct {
	mu      sync.Mutex
	at      time.Time
	widget  bool
	fills   bool
	loading bool
}

func (c *flagCache) invalidate() {
	c.mu.Lock()
	c.at = time.Time{}
	c.mu.Unlock()
}

// ownerFlags returns (widget enabled, fill pushes enabled) for the single owner.
func (s *Server) ownerFlags(ctx context.Context) (bool, bool) {
	c := &s.flags
	c.mu.Lock()
	if !c.at.IsZero() && s.now().Sub(c.at) < 15*time.Second || c.loading || s.store == nil || s.store.pool == nil {
		w, f := c.widget, c.fills
		c.mu.Unlock()
		return w, f
	}
	c.loading = true
	c.mu.Unlock()
	widget, fills := false, false
	if stored, err := s.store.settings(ctx, s.cfg.OwnerID); err == nil {
		if row, ok := stored["ui.tradingview_widget_enabled"]; ok {
			_ = json.Unmarshal(row.Value, &widget)
		}
		if row, ok := stored["alerts.push_fills_enabled"]; ok {
			_ = json.Unmarshal(row.Value, &fills)
		}
	}
	c.mu.Lock()
	c.widget, c.fills, c.at, c.loading = widget, fills, s.now(), false
	c.mu.Unlock()
	return widget, fills
}

func validateSetting(spec settingSpec, raw json.RawMessage) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	switch spec.kind {
	case "bool":
		var b bool
		if err := dec.Decode(&b); err != nil {
			return nil, errors.New("value must be a boolean")
		}
		return b, nil
	case "enum":
		var v string
		if err := dec.Decode(&v); err != nil {
			return nil, errors.New("value must be a string")
		}
		for _, e := range spec.enum {
			if v == e {
				return v, nil
			}
		}
		return nil, errors.New("value not allowed")
	default:
		return nil, errors.New("unsupported setting kind")
	}
}
