// Hand-written (not generated): type-checked by `pnpm --filter @strawberry/contracts typecheck`
// to prove the generated TypeScript compiles against the pinned @bufbuild/protobuf runtime.
import { create, toBinary, fromBinary } from "@bufbuild/protobuf";
import { Mode, Side } from "../src/tb/common/v1/common_pb";
import { OrderIntentSchema } from "../src/tb/orders/v1/orders_pb";

const intent = create(OrderIntentSchema, {
  intentId: "01JABCDEFGHJKMNPQRSTVWXYZ0",
  strategyId: "manual",
  side: Side.BUY,
  mode: Mode.PAPER,
  sizing: { case: "quantity", value: { value: "10" } },
});

export const roundTripped = fromBinary(OrderIntentSchema, toBinary(OrderIntentSchema, intent));
