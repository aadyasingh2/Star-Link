# Stability-Aware Predictive Routing in a LEO Satellite Constellation

**Draft.** Items marked `[FILL: ...]` need numbers from `results/comparison_600s.txt`. Everything else in this draft comes from the code, the configuration, or results already checked.

## Abstract

Low-Earth-orbit constellations change shape every second, and reactive routing protocols only learn about a change after it happens. Because satellite motion is predictable, a router could avoid links that are about to disappear. We built a discrete-event simulator of a 528-satellite constellation with five ground stations, implemented Link State, Distance Vector and a predictive router (SAPR), and compared them against an oracle that knows the true topology. Over a 600 s run, the reactive routers chose a path within 1 ms of optimal in 80.9% (Link State) and 79.9% (Distance Vector) of samples, against 72.4% for SAPR. [FILL: one sentence on blackholes, route changes per minute and control overhead, from the table.]

## 1. Introduction

LEO topology changes follow orbital mechanics. This project asks whether routing that uses that knowledge beats reactive Link State and Distance Vector protocols on path delay, route stability and control overhead. The routers are compared on the same topology, the same traffic pairs, and the same seed.

Scope. Packet queues, packet loss beyond route-level probes, and TCP throughput are not measured (Section 7).

## 2. System model

**Constellation.** 24 orbital planes with 22 satellites each (528 satellites) at 550 km altitude and 53 degrees inclination, phasing factor 1. Orbits are circular, positions come from Kepler's third law, and the Earth rotates beneath the constellation (ECI to ECEF).

**Inter-satellite links.** Each satellite keeps links to its two neighbours in its own plane and to the nearest satellite in the adjacent plane (a +Grid pattern). No link crosses the seam between the first and last plane. A link exists only when the satellites are within 3000 km, the line of sight clears the Earth plus an 80 km atmosphere margin, and, for inter-plane links, both satellites are below 45 degrees latitude. The latitude cutoff is what makes the topology change over time: intra-plane links are constant, inter-plane links come and go.

**Ground stations.** Chennai, London, New York, Tokyo and Sydney. A ground station connects to every satellite at least 25 degrees above its horizon.

**Delay.** Propagation delay of a link is its length divided by the speed of light. Topology is sampled once per second.

## 3. Simulator and routers

**Engine.** A heap-based event queue with a sequence-number tie-break and cancellable timers. Topology events come from comparing the snapshots at t-1 and t and are stamped at t; routers hear about them after a detection delay of 0.1 s. The engine never looks ahead. Before the measured run, both reactive routers converge on the t=0 topology; those boot messages are counted and reported separately and are excluded from overhead.

**Message model.** Every routing message takes the actual propagation delay of its link at send time plus 1 ms of processing per hop. A message is sent only over a link that is up at send time, and is dropped on delivery if the link has gone down in flight. Ground stations never relay traffic: they can be a source or a destination, not a transit node.

**Link State.** Each router floods sequence-numbered link-state advertisements, keeps a database, and runs a heap-based Dijkstra over its own database only. SPF is lazy: a router marks its table dirty when its database changes and recomputes only when a route is requested after a 1 s hold-down. Costs are advertised when a link comes up or when its delay differs from the last advertised value by more than 2 ms.

**Distance Vector.** Each router holds numpy cost and next-hop tables and sends vectors periodically (30 s, with per-node jitter) and when its table changes. Triggered updates are damped to at most one per second per node. Poison reverse is applied when a vector is sent: any destination whose next hop is the receiving neighbour is advertised at the infinity metric (10000 ms).

Damping was a performance and realism choice. Without it, booting the real constellation produced about 919,000 messages and took over 220 s; with it, about 57,000 messages and about 11 s.

**SAPR (our design).** SAPR assumes every node can compute the orbital future locally, so it sends no routing messages. For each link it looks ahead up to 300 s and measures how long the link will stay up. A link's cost is its delay scaled by `1 + alpha * max(0, 1 - lifetime / window)` with alpha = 0.5, so short-lived links look more expensive. Links expiring within a 5 s guard time are excluded (with a fallback if that disconnects the destination). A route is kept unless a new one is better by more than a 10 ms hysteresis margin, and a path is rejected if its delay exceeds 1.2 times the shortest path. SAPR runs Dijkstra on the true snapshot at the query time.

## 4. Evaluation method

**Oracle.** Shortest path by true delay on the true snapshot, with ground stations as endpoints only.

**Sample classes.** Each second, for each ground-station pair and each router, the route is classified as exactly one of: correct (the oracle path or an equal-delay path), stale-but-working (valid but not best), loop, or blackhole (no route, or any link of the route is missing from the true snapshot).

**Metrics.** Percentage of samples in each class; percentage within 1 ms of the oracle delay (near-optimal); mean and 99th-percentile actual path delay and stretch over valid paths; route changes per minute; control messages and bytes (from the message-size model in `config.yaml`); dropped messages; boot messages.

**Setup.** 600 s at a 1 s step, latitude cutoff 45 degrees, seed 42. [FILL: number of ground-station pairs and which. The baseline report used Chennai-London, Chennai-Sydney and New York-Tokyo; the visualization export covers ten pairs. Confirm which the reported table uses.]

**Reproducibility.** Iteration over nodes and edges is sorted and DV jitter is drawn per node, so results do not depend on Python's hash seed. Two 120 s runs under different hash seeds produced identical output files.

## 5. Results

| Router | %correct | %near-optimal | %stale | %loop | %blackhole | mean delay (ms) | p99 delay (ms) | stretch | route changes/min | control msgs | control bytes | boot msgs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Oracle | 100.00 | 100.00 | 0.00 | 0.00 | 0.00 | [FILL] | [FILL] | 1.000 | [FILL] | 0 | 0 | 0 |
| LinkState | 54.17 | 80.89 | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] |
| DistanceVector | 53.50 | 79.89 | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] |
| SAPR | 32.56 | 72.39 | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | [FILL] | 0 | 0 | 0 |

*Table 1. 600 s comparison, seed 42. Source: `results/comparison_600s.txt`.*

[FILL: per-pair table or one paragraph, from the per-pair breakdown.]

## 6. Discussion

**Path quality.** Link State and Distance Vector are close: they differ by 0.7 points on strict correctness and 1.0 point on near-optimality. With a single seed and window we do not claim one is better. SAPR has the lowest strict correctness (32.6%) and the lowest near-optimality (72.4%), so in this configuration it does not improve delay-optimality.

**Why "correct" is low for every router.** The strict class requires the exact oracle path or an equal delay. A path that is 0.1 ms longer counts as stale-but-working. Near-optimality is the fairer headline measure; both are reported. For Link State, the diagnostic block of the report lists stale samples where a router's stored link cost differs slightly from the true delay (for example 4.036 ms stored against 4.143 ms true). That is consistent with the 2 ms advertisement threshold: small delay drift is not re-advertised, so the router's view lags the truth by a small amount. [FILL: confirm against the diagnostic block in the final file.]

**SAPR's trade-off.** SAPR deliberately pays some delay to avoid links that will soon disappear. Because it routes on the true snapshot at query time, its blackhole and loop counts are zero by construction in this implementation; they are not an experimental result. The informative SAPR numbers are delay stretch and route changes per minute. [FILL: route changes per minute for SAPR against LinkState and DistanceVector, and the stretch values. State whether the stability gain justifies the delay cost.]

**Reactive staleness.** With a 0.1 s detection delay and a 1 s hold-down, the reactive routers lose little time per failure, so blackhole samples are rare. [FILL: blackhole percentages for LinkState and DistanceVector.]

**Control overhead.** Link State floods small advertisements; Distance Vector sends whole vectors. [FILL: messages and bytes for each, and the ratio. Boot messages are reported separately and excluded.] SAPR's zero overhead depends on the assumption that orbit predictions are available at every node for free.

**Cost-drift rule.** The 2 ms advertisement threshold is implemented and tested on small graphs. In a check over the first 60 s, the largest delay change on any persistent link was 0 ms for intra-plane links, 0.186 ms for inter-plane links and 1.060 ms for ground links, all below the threshold. [FILL: cost events generated in the 600 s run, from the table's cost-event column.] Over this window, link churn dominates over delay drift as a source of control traffic.

## 7. Limitations

- SAPR is evaluated with perfect orbit prediction and no signalling; real systems have prediction error and must distribute ephemeris data.
- Delay and loss are route-level measurements, not a packet simulation with queues. TCP throughput is not measured.
- One seed, one 600 s window, one latitude cutoff (45 degrees); no confidence intervals.
- Circular orbits, ground stations at altitude 0, a fixed message-size model.
- Triggered-update damping, hold-down and detection delay are modelling choices that affect the comparison.
- The SAPR formula is our design, not a published algorithm.

## 8. Conclusion

In this simulation the reactive routers stayed within about a millisecond of optimal in roughly four out of five samples, and the predictive router traded delay for stability at no signalling cost. [FILL: final sentence once the stability and overhead numbers are in the table.] Future work: a packet-level simulation with queues and TCP, multiple seeds and cutoffs, a sweep over SAPR's alpha, and prediction error.

## Appendix A. Configuration

Constellation: 24 planes, 22 satellites per plane, 550 km, 53 degrees, phasing 1, ISL range 3000 km, minimum elevation 25 degrees, inter-plane latitude cutoff 45 degrees.
Routing: detection delay 0.1 s, LSA and DV processing 1 ms, SPF hold-down 1 s, DV period 30 s, DV triggered hold-off 1 s, poison reverse on, DV infinity 10000 ms, cost-change threshold 2 ms, boot window 60 s.
SAPR: look-ahead 300 s, alpha 0.5, guard time 5 s, hysteresis 10 ms, latency bound 1.2.
Evaluation: near-optimal tolerance 1 ms, seed 42.