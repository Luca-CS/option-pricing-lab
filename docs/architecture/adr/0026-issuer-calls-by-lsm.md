# ADR-0026: Issuer calls by regression in the payoff language

- Status: Accepted, 27 November 2026

## Context

An issuer-callable note lets the issuer redeem it on call dates, at a price fixed in the sheet. The issuer calls when
the note costs it more to keep than to redeem: when the note's continuation, seen from the issuer, is above the call
price. Unlike an autocall, whose trigger is a condition on the spot written in the script, the call is a decision that
depends on the value of the rest of the product. A forward Monte Carlo cannot read that value on a path.

The payoff language, its vectorised evaluator, the batches on their seeds, the engines and the risk engine's common
random numbers had to stay as they are, and a script without a call had to keep its numbers bit for bit.

## Decision

- **Syntax.** `call pay <amount>` at a call date: the issuer may redeem here, paying the amount as the redemption, and
  the product ends. `state a, b` names what the issuer decides on, variables of the script or spots (the spots by
  default). Both are words of the language only where they open the statement or the declaration, so a script that
  used `call` or `state` as a name reads as before. At most one call per event; the state must be known at each call.
- **An uncalled run and its record.** The evaluator runs the script as if the issuer never called. It keeps each event's
  flows before and after its call statement, and at each call the mask, the alive mass, the amount and the state.
  Calling is paying the amount and terminating, so the flows under any policy are rebuilt exactly from that record (a
  survival factor per path). The evaluation itself is unchanged.
- **The policy.** It is fitted by backward regression (Longstaff-Schwartz) on a pilot batch:
  - The pilot has its own seed, a child of the pricing seed that no batch takes, and half the paths (20,000 to 100,000), on NumPy whatever the engine.
  - At each call date, the continuation is the note's flows after the call, discounted as the note discounts them, so on the issuer's
    curve. It is regressed on polynomials of the state and on hinges at its deciles, which follow the kinks of a
    coupon or knock-in barrier.
  - The issuer calls where the fitted continuation is above the call amount.
- **Out of sample.** The policy is applied to the pricing batches, so it is one the issuer could follow and never better than the best. The
  note's value is an upper bound of its value under the optimal call. The pilot's own value, biased the other way, is kept beside it.
- **Frozen for the risk.** A run's spot bumps keep the base's policy. A caller passes a result's policy to keep it for
  any other bump (the envelope theorem: the policy's first-order change is worth nothing, and common random numbers keep the Greeks smooth).
- **The dual bound, on request.** Andersen-Broadie's lower bound, from inner continuations branched at each call date
  from each outer path's history, so any path dependence of the script is kept. It is computed on a single name, under a model without a variance process. Its gap to
  the policy's value is what the policy may leave to the issuer, a reserve candidate.
- **The class.** `issuer_callable` (the autocall keeps `callable`), priced by local vol Monte Carlo with the policy,
  SLV as its provision.

## Consequences

- A script without a call takes exactly the code it took: the goldens stay bit for bit.
- The engines need nothing new: the policy is a function of the state, applied to whatever paths a batch holds.
- A callable's price is no longer affine in its coupon once the policy depends on it: the coupon is solved by refitting
  the policy and a secant on the coupon.
- References: a Crank-Nicolson PDE in log spot (Black-Scholes or local vol, Bermudan calls). The policy is checked against it within 4
  standard errors plus 10 bp, and never below it.

## Alternatives considered

- **A PDE pricer for callables.** Exact in one or two dimensions, but every product would need its own grid. It is kept as the reference.
- **The policy fitted in sample, on the pricing paths.** One simulation fewer, but its foresight biases the price toward the issuer
  by an amount nobody can bound, and the Greeks would move with the refit. Refused.
- **A stopping rule written in the script** (call above a spot level). It is simple, but the level would be the structurer's guess,
  not the issuer's decision, and the value would depend on it.
