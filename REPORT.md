# Stability-Aware Predictive Routing in a LEO Satellite Constellation

## Abstract

This project implements a discrete-event routing simulator for a 528-satellite, five-ground-station low-Earth-orbit constellation. It compares Link State and Distance Vector (reactive routers), SAPR (a predictive routing design), and an oracle that selects the shortest-delay route on the current topology. In the 600-second, seed-42 comparison across three ground-station pairs, Link State and Distance Vector were within 1 ms of oracle delay in 80.89% and 79.89% of samples; SAPR was within 1 ms in 72.39%. SAPR had the fewest route changes (1.9/min versus 2.4 for Link State and 3.8 for Distance Vector) and, by design, used no signalling. These results describe one deterministic simulation configuration, not operational satellite-network performance.

## 1. Research question and scope

LEO topology changes follow orbital motion. This project asks whether using predicted link lifetime can trade a small amount of path quality for route stability and lower signalling overhead relative to reactive Link State and Distance Vector routing.

The primary comparison is produced by `scripts/baseline_report.py` and uses the three pairs Chennai-London, Chennai-Sydney, and New York-Tokyo. The web visualization exporter evaluates all ten unordered pairs of the five ground stations. Unless a result is explicitly labelled as per-pair, the numerical results below refer to the three-pair baseline report.

## 2. System model

**Constellation.** The configuration has 24 orbital planes with 22 satellites each (528 satellites), at 550 km altitude and 53 degrees inclination, with phasing factor 1. Positions are generated from circular orbital motion and transformed from ECI to ECEF as the Earth rotates.

**Inter-satellite links.** Satellites connect to their two neighbours in the same plane and to a nearby satellite in an adjacent plane. There is no link across the first/last-plane seam. Links are constrained by a 3000 km maximum range, a line-of-sight clearance margin, and a 45-degree inter-plane latitude cutoff.

**Ground stations.** The model includes Chennai, London, New York, Tokyo, and Sydney at the configured latitude/longitude coordinates. A station connects to satellites that meet the 25-degree minimum elevation threshold. Ground stations can be path endpoints, not transit nodes.

**Delay.** Link propagation delay is derived from link distance and the speed of light. The topology is sampled at one-second intervals. The simulator does not model packet queues or TCP.

## 3. Simulator and routing methods

**Event engine.** A heap-based discrete-event engine orders events with a sequence-number tie-break and supports cancellable timers. Topology changes are detected between snapshots and delivered to routers after the configured 0.1-second detection delay. Reactive routers first converge on the frozen time-zero topology; boot messages are reported separately from measured-run messages.

**Message model.** Routing messages incur propagation delay on the sending link and 1 ms of per-hop processing delay. A message is sent only when its link is up and is dropped if that link goes down before delivery. Message bytes use the configured fixed header and entry sizes.

**Link State.** Routers flood sequence-numbered link-state advertisements and run Dijkstra over their local link-state database. SPF work is subject to a 1-second hold-down. An updated link cost is advertised when the link comes up or the cost changes by more than 2 ms.

**Distance Vector.** Routers exchange cost/next-hop vectors periodically (every 30 seconds with per-node jitter) and on triggered updates. Triggered updates are held off for 1 second per node. Poison reverse is applied when sending vectors, with 10000 ms as the infinity metric.

**SAPR (project design).** SAPR assumes each node can predict future orbital geometry locally, without signalling or prediction error. It evaluates link lifetimes over a 300-second look-ahead window, penalizes short-lived links using alpha 0.5, excludes links expiring within a 5-second guard time when feasible, applies a 10 ms route hysteresis, and limits selected-path delay to 1.2 times the shortest path. SAPR computes against the true current topology at query time. Its zero control-message count is therefore an assumption of this design, not a measured deployment result.

## 4. Evaluation and reproducibility

The baseline run uses seed 42, a 1-second step, a 600-second duration, the 45-degree inter-plane cutoff, and the three pairs listed in Section 1. The three pairs each contribute 600 samples, or 1800 pair-seconds per router. Percentages use oracle-reachable samples as their denominator.

Each route is classified as exactly one of:

- **correct**: the oracle path or a path with equal actual delay;
- **stale_working**: a valid route that is not the best route;
- **loop**: forwarding follows a cycle;
- **blackhole**: no usable path, or a path includes a link absent from the true snapshot.

Actual path delay is calculated using true link delays. Near-optimal means the route is valid and its actual delay is no more than 1 ms above the oracle delay. Mean/p99 delay and mean stretch are computed over routes with actual delay. Route changes are reported per minute. Control messages and bytes use the modelled message sizes; boot messages are a separate counter.

The routing code sorts order-sensitive iteration and derives Distance Vector jitter per node, rather than consuming one shared random stream. Two 120-second runs under `PYTHONHASHSEED=1` and `PYTHONHASHSEED=2` produced identical summary and per-sample CSVs. The exact generated 600-second comparison and per-sample data are in `results/comparison_600s.txt`, `results/comparison_600s.csv`, and `results/comparison_600s_samples.csv`.

## 5. Results

### Table 1. Three-pair baseline results

| Router | %correct | %stale_working | %loop | %blackhole | %near_optimal | mean actual delay ms | p99 actual delay ms | mean stretch vs oracle | Route changes/min (summed over the 3 pairs) | measured control messages | control bytes | dropped messages | SPF runs | cost events generated | boot messages | Avg route changes/min/pair |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Oracle | 100.00 | 0.00 | 0.00 | 0.00 | 100.00 | 47.680 | 60.304 | 1.000 | 3.800 | 0 | 0 | 0 | - | 0 | 0 | 1.267 |
| Link State | 54.17 | 44.50 | 0.00 | 1.33 | 80.89 | 48.113 | 60.638 | 1.009 | 2.400 | 731,266 | 37,246,856 | 0 | 3,595 | 0 | 620,426 | 0.800 |
| Distance Vector | 53.50 | 45.67 | 0.00 | 0.83 | 79.89 | 48.162 | 60.613 | 1.010 | 3.800 | 367,503 | 1,073,615,616 | 0 | - | 0 | 57,650 | 1.267 |
| SAPR | 32.56 | 67.44 | 0.00 | 0.00 | 72.39 | 48.605 | 61.753 | 1.018 | 1.900 | 0 | 0 | 0 | - | 0 | 0 | 0.633 |

Source: `results/comparison_600s.csv`. Route changes/min is summed over pairs; the final column is the per-pair average rounded to three decimals. Boot-message counters are separate from measured-run control messages.

### Table 2. 10-pair results

| Router | %correct | %stale_working | %loop | %blackhole | %near_optimal | mean actual delay ms | p99 actual delay ms | mean stretch vs oracle | Route changes/min (summed over the 10 pairs) | measured control messages | control bytes | dropped messages | SPF runs | cost events generated | boot messages | Avg route changes/min/pair |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Oracle | 100.00 | 0.00 | 0.00 | 0.00 | 100.00 | 50.176 | 108.225 | 1.000 | 12.700 | 0 | 0 | 0 | - | 0 | 0 | 1.270 |
| Link State | 49.67 | 49.12 | 0.00 | 1.22 | 77.72 | 50.664 | 109.541 | 1.012 | 8.900 | 731,266 | 37,246,856 | 0 | 10,214 | 0 | 620,426 | 0.890 |
| Distance Vector | 49.00 | 49.70 | 0.18 | 1.12 | 76.63 | 50.666 | 109.650 | 1.016 | 14.000 | 367,503 | 1,073,615,616 | 0 | - | 0 | 57,650 | 1.400 |
| SAPR | 39.58 | 60.42 | 0.00 | 0.00 | 68.75 | 51.281 | 109.538 | 1.022 | 6.400 | 0 | 0 | 0 | - | 0 | 0 | 0.640 |

Table 2 is generated from `web/public/data/summary.json`; it uses the same metric columns as Table 1. The route-change heading gives the pair count for each table.

**Scope of the numbers.** Table 1 and the per-pair table above use three pairs: Chennai-London, Chennai-Sydney, and New York-Tokyo. The web app's Analytics tab uses all ten station pairs from the same seed, the same 600-second run, and the same simulated network. Control messages, control bytes, dropped messages, and boot messages are identical in both outputs because those simulation counters do not depend on which pairs are evaluated. Router ordering is the same for %near_optimal and %blackhole, including ties. For route changes/min, Oracle and Distance Vector tie in the three-pair data, while Distance Vector is higher than Oracle in the ten-pair data.

### Per-pair results

Each class cell gives correct/stale_working/loop/blackhole sample counts out of 600. Delay is mean actual delay in milliseconds.

| Pair | Router | Class counts (C/S/L/B) | Near-optimal | Mean delay (ms) |
|---|---|---:|---:|---:|
| Chennai-London | Oracle | 600/0/0/0 | 100.00% | 33.526 |
| Chennai-London | Link State | 585/7/0/8 | 98.00% | 33.535 |
| Chennai-London | Distance Vector | 578/18/0/4 | 97.33% | 33.617 |
| Chennai-London | SAPR | 433/167/0/0 | 98.33% | 33.770 |
| Chennai-Sydney | Oracle | 600/0/0/0 | 100.00% | 50.826 |
| Chennai-Sydney | Link State | 205/388/0/7 | 51.17% | 51.709 |
| Chennai-Sydney | Distance Vector | 203/391/0/6 | 50.83% | 51.728 |
| Chennai-Sydney | SAPR | 3/597/0/0 | 42.17% | 52.091 |
| New York-Tokyo | Oracle | 600/0/0/0 | 100.00% | 58.689 |
| New York-Tokyo | Link State | 185/406/0/9 | 93.50% | 59.109 |
| New York-Tokyo | Distance Vector | 182/413/0/5 | 91.50% | 59.171 |
| New York-Tokyo | SAPR | 150/450/0/0 | 76.67% | 59.953 |

Source: the per-pair breakdown in `results/comparison_600s.txt`. There were no oracle-unreachable samples for these three pairs in this run.

### New York-Tokyo Link State diagnostic

The first five `stale_working` Link State samples occur at t=33, 34, 35, 36, and 37 seconds. In all five samples, the oracle route is:

`gs_New York -> sat_227 -> sat_226 -> sat_225 -> sat_224 -> sat_223 -> sat_222 -> sat_201 -> sat_200 -> sat_179 -> sat_178 -> gs_Tokyo`

The Link State route in each sample is:

`gs_New York -> sat_227 -> sat_206 -> sat_205 -> sat_184 -> sat_183 -> sat_182 -> sat_181 -> sat_180 -> sat_179 -> sat_178 -> gs_Tokyo`

| t (s) | Oracle delay (ms) | Link State delay (ms) | sat_227->sat_206 true/stored (ms) | sat_205->sat_184 true/stored (ms) |
|---:|---:|---:|---:|---:|
| 33 | 59.621 | 59.626 | 4.137 / 4.036 | 4.104 / 4.002 |
| 34 | 59.617 | 59.634 | 4.140 / 4.036 | 4.107 / 4.002 |
| 35 | 59.614 | 59.643 | 4.143 / 4.036 | 4.110 / 4.002 |
| 36 | 59.611 | 59.653 | 4.146 / 4.036 | 4.113 / 4.002 |
| 37 | 59.608 | 59.663 | 4.149 / 4.036 | 4.116 / 4.002 |

The other Link State path links listed by the diagnostic have equal true and stored costs in these samples. The two costs above have drifted, but each individual change remains below the configured 2 ms advertisement threshold; this is consistent with the router retaining its earlier advertised values.

## 6. Interpretation

Link State and Distance Vector have similar overall results: their near-optimal rates differ by 1.00 percentage point and their mean stretch is 1.009 versus 1.010. They are not uniformly similar across pairs: for Chennai-Sydney both are near-optimal in about 51% of samples, while for Chennai-London both exceed 97%.

SAPR reduces route changes to 1.9 per minute, compared with 2.4 for Link State and 3.8 for Distance Vector. It also has higher mean stretch (1.018) and lower overall near-optimality (72.39%). In this configuration, the stability benefit comes with a measurable path-quality trade-off; this single run cannot establish that the trade-off is worthwhile in a real network.

The reactive routers have low but nonzero blackhole rates: 1.33% for Link State and 0.83% for Distance Vector. SAPR's zero loop and blackhole values are expected from the implementation's use of the true topology at route-query time and should not be interpreted as a deployed-system reliability result.

Distance Vector sent fewer measured messages than Link State (367,503 versus 731,266), but its modelled byte count was much larger (about 28.8 times Link State) because vectors carry destination entries. Link State also has a much larger separate boot-message count (620,426 versus 57,650). SAPR's zero message and byte counts depend on assuming perfect local prediction with no ephemeris distribution cost.

Distance Vector produced transient loops on London-Tokyo in the 10-pair data: 11 loop samples, contributing to the 0.18% aggregate loop rate.

The run reports zero generated cost events for the 2 ms threshold. In a separate first-60-second check documented with the simulation results, the largest persistent-link delay change was 0 ms for intra-plane links, 0.186 ms for inter-plane links, and 1.060 ms for ground links. This supports the absence of threshold-crossing cost events in that checked window; the 600-second report's zero cost-event count is the result for the full run.

## 7. Limitations

- One seed, one 600-second window, one latitude cutoff, and no confidence intervals or multi-seed sensitivity analysis.
- SAPR assumes perfect orbit prediction at every node, zero prediction error, and no signalling cost.
- Measurements are route-level probes. There is no packet-level queueing, packet-loss model beyond route availability, or TCP throughput result.
- Circular-orbit and ground-station-at-zero-altitude assumptions simplify the physical model.
- Message sizes are a configured accounting model; they are not measured radio or protocol implementation costs.
- The SAPR metric and policy are this project's design, not a claim to reproduce a standard or published protocol.
- The browser app visualizes exported simulation data; it does not run the Python simulation interactively or connect to a live API/database.

## Mapping to the project spec

| Spec metric | Where measured | Status |
|---|---|---|
| End-to-end latency | Mean and p99 actual path delay in the baseline report and `results/comparison_600s.csv`; route-level | Measured |
| Packet loss at handover | Blackhole and loop sample percentages plus dropped control messages in the baseline report; route-level proxy, not a packet simulation | Proxy only; no packet simulation |
| Route changes per minute | Baseline route-path change counter, reported per minute | Measured |
| Control messages | Baseline control-message counters; control bytes and boot messages reported separately | Measured using the configured accounting model |
| TCP throughput | Not produced by the simulator | Not implemented; future work |

The `src/sim` and `src/tcp` packages are intentionally empty placeholder packages.

## 8. Conclusion

For this seed and three-pair, 600-second experiment, reactive Link State and Distance Vector are within 1 ms of oracle delay in roughly four out of five samples. SAPR is less near-optimal overall but changes routes less often and has zero modelled signalling under its perfect-prediction assumption. The results demonstrate a stability/quality trade-off in this simulator; broader claims require multiple seeds, more scenarios, prediction error, and packet-level evaluation.

## Appendix A. Configuration

Constellation: 24 planes, 22 satellites per plane, 550 km altitude, 53-degree inclination, phasing 1, 3000 km ISL range, 25-degree minimum ground-station elevation, and 45-degree inter-plane latitude cutoff.

Simulation: seed 42; baseline duration 600 s at a 1 s step. The baseline script's duration default is 600 s; the general configuration file's `simulation.duration_s` is 3600 s and is not the duration used by that default baseline invocation.

Routing: 0.1 s detection delay; 1 ms LSA and DV processing; 1 s SPF hold-down; 30 s DV update period; 1 s triggered-update hold-off; poison reverse enabled; 10000 ms DV infinity; 2 ms cost-change threshold; 60 s boot window.

SAPR: 300 s look-ahead; alpha 0.5; 5 s guard time; 10 ms hysteresis; 1.2 latency-bound multiplier. Near-optimal tolerance: 1 ms.
