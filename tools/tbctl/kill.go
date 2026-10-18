package main

import (
	"context"
	"crypto/rand"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/oklog/ulid/v2"

	ordersv1 "github.com/illyanyc/strawberry/contracts/gen/go/tb/orders/v1"
	"github.com/illyanyc/strawberry/libs/go/bus"
	"github.com/illyanyc/strawberry/libs/go/secrets"
)

// killEngage publishes a latched kill-switch engagement on risk.kill (RM-006): the server-side
// path when the UI or gateway is down. Releases are not possible from the CLI (RM-007).
func killEngage(ctx context.Context, store *secrets.Store, args []string) error {
	fs := flag.NewFlagSet("kill engage", flag.ContinueOnError)
	scope := fs.String("scope", "global", "global | broker_account | strategy | instrument")
	target := fs.String("target", "", "account, strategy, or instrument ID (empty for global)")
	action := fs.String("action", "cancel_open", "freeze | cancel_open | flatten_strategy_owned | demote_to_paper")
	reason := fs.String("reason", "", "why (required)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	scopes := map[string]ordersv1.KillSwitchScope{"global": ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL, "broker_account": ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_BROKER_ACCOUNT,
		"strategy": ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_STRATEGY, "instrument": ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_INSTRUMENT}
	actions := map[string]ordersv1.KillSwitchAction{"freeze": ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_FREEZE, "cancel_open": ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_CANCEL_OPEN,
		"flatten_strategy_owned": ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_FLATTEN_STRATEGY_OWNED, "demote_to_paper": ordersv1.KillSwitchAction_KILL_SWITCH_ACTION_DEMOTE_TO_PAPER}
	sc, ok1 := scopes[*scope]
	ac, ok2 := actions[*action]
	if !ok1 || !ok2 || *reason == "" || (sc != ordersv1.KillSwitchScope_KILL_SWITCH_SCOPE_GLOBAL && *target == "") {
		fs.Usage()
		return fmt.Errorf("invalid scope, action, target, or missing reason")
	}
	nc, err := bus.Connect(bus.Config{URL: env("TB_NATS_URL", "nats://nats:4222"), User: "ops"}, store, "tbctl")
	if err != nil {
		return err
	}
	defer nc.Close()
	js, err := jetstream.New(nc)
	if err != nil {
		return err
	}
	host, _ := os.Hostname()
	ev := &ordersv1.KillSwitchEvent{EventId: ulid.MustNew(ulid.Now(), rand.Reader).String(), Scope: sc, Target: *target, Action: ac,
		Actor: "cli:ops@" + host, Reason: *reason, OccurredAt: time.Now().UnixNano(), Latched: true}
	if err := bus.Publish(ctx, js, "risk.kill", "kill:"+ev.GetEventId(), ev); err != nil {
		return err
	}
	fmt.Printf("kill switch %s published (%s %s %s); execution latches it on receipt\n", ev.GetEventId(), *scope, *target, *action)
	return nil
}
