"""Hiring/employment scenarios.

Three scenarios stress different handoffs of a multi-agent hiring pipeline
(sourcing -> parsing -> screening -> async-interview -> ranking ->
decision-support):

  * procurement          -- governance at the sourcing/parsing -> screening
                            handoff (verifiers exercised at procurement time)
  * inflight_oversight   -- meaningful human oversight at the screening ->
                            recruiter handoff (Quadripartite forwarding;
                            semantic statements replace raw video)
  * cohort_audit         -- post-hoc cohort review at the deployer -> regulator
                            corpus boundary (feature-usage census + four-fifths
                            disparate-impact test)

Each scenario produces signed envelopes, a W3C PROV graph, and an audit report
under ``usecases/output/``.
"""
