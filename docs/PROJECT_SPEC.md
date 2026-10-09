# Stability-Aware Predictive Routing for LEO Satellite Constellations (SAPR)

## Research Question
LEO topology changes are predictable from orbital mechanics. Can routing that uses this knowledge beat reactive Link State and Distance Vector protocols on latency, packet loss at handover, route stability, and control overhead?

## Module List
* `src/constellation`: Satellite orbit propagation and topology generation
* `src/routing`: Implementation of different routing algorithms
* `src/sim`: Core discrete-event simulation engine
* `src/tcp`: TCP transport layer implementation
* `src/experiments`: Scripts to run different simulation scenarios
* `src/viz`: Visualization tools for metrics and topology

## Metrics
1. **End-to-End Latency:** Average and 99th percentile packet delay.
2. **Packet Loss During Handover:** Drops caused by link breakages and route convergence.
3. **Route Changes Per Minute:** Frequency of routing table updates (route stability).
4. **Control Messages:** Network overhead caused by routing protocol traffic.
5. **TCP Throughput:** Effective data rate experienced by applications.

## Routers to Compare
1. **LinkState (Reactive):** Standard OSPF-like shortest path routing that reacts to link failures.
2. **DistanceVector (Reactive):** Standard RIP/BGP-like routing reacting to distance metrics.
3. **SAPR (Predictive):** Stability-Aware Predictive Routing using orbital mechanics to pre-compute paths.
