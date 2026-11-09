# Test baseline and measured bottlenecks

## Snapshot

Baseline was collected on 2026-11-09 from commit `185c6a6` (`origin/main`) on Linux, before test-tier edits. The checkout was clean. Pytest collected 2,105 parametrized test items from 162 test modules. The initial full run took 2,748 seconds (45:48) and produced 1,951 passed, 14 failed, and 140 skipped. This is the measured baseline for this checkout; the earlier 1,952/13/140 reference is stale by one failure.

The initial refactor-only collection contained 2,107 items in 163 test modules. The two new items exercised the collection guard; no baseline item was removed. The full-tier runner uses an unfiltered collection and reports all default-to-L3 cases.

## Latest remote main refresh

The branch was rebased onto `origin/main` commit `fe7d0d3` (14 commits beyond the original baseline). Collection-only verification on the rebased code reports 2,155 items in 174 modules: 162 unit, 423 integration, and 1,570 full. This is 48 more existing test items than the original baseline plus the two collection-guard tests added by this refactor. No collected item is filtered from L3.

The new remote tests were added after the original 2,748-second baseline. The full pre-refactor suite was not rerun at `fe7d0d3`; the latest branch's L2 and L3 were also not rerun at the user's direction. The latest `--no-diff` L1 did run: 162 passed, 0 failed, in 19.18 seconds of pytest time, with Ruff passing. The attempted diff-aware Quick widened to L2 and was interrupted before completion; it is not a PASS. Treat all slowest-test timings below as measurements from the original `185c6a6` baseline or the pre-refresh refactor run, not as a profile of `fe7d0d3`.

| Baseline measure | Result |
| --- | ---: |
| Test modules / files | 162 |
| Collected items | 2,105 |
| Pass | 1,951 |
| Fail | 14 |
| Skip | 140 |
| Linux wall time | 2,748 s (45:48) |
| Ruff | Not part of the initial baseline command |

## Initial failure attribution

All 14 failures were already present before the refactor. The run showed test-fixture/platform mismatches and expected Windows-only guards, rather than a new product regression:

| Cause seen in the failure evidence | Count | Examples |
| --- | ---: | --- |
| Fake deployment host retained the executor's Linux OS identity | 4 | `test_wizard_install_payload_is_accepted`, `test_full_deployment_budget_includes_environment_and_snapshots`, `test_a_qualified_host_gets_a_plan_that_records_the_variant`, `test_next_instance_plan_state_machine` |
| Image catalog test used actual host disk capacity instead of its declared 200 GiB fixture budget | 1 | `test_the_product_can_plan_the_image_weight_installation` (`INSUFFICIENT_DISK_ON_HOST`) |
| Windows-only service and managed renderer routes correctly returned their platform guard before the later assertion | 7 | Six `test_service_validation.py` cases and `test_an_unmanaged_instance_is_refused` |
| Local executor contract test ran on Linux, where local execution remains disabled even when its opt-in variable is set | 2 | `test_shipped_service_uses_real_install_contract`, `test_shipped_service_uses_real_instance_approval_and_state_machine` |

The refactor repairs the test host facts and disk fixture, and marks the latter nine Windows execution checks for Windows L3. Product OS and execution gates are unchanged. Linux skips are not counted as Windows evidence; the Windows CI run remains necessary.

## Suite inventory and shared resources

The latest pytest collection guard reports 2,155 items: 162 L1 unit items, 423 L2 integration items, and 1,570 L3 full items. Of the full items, 1,546 are intentionally visible as defaulted-to-L3 until their scope is reviewed; 24 have an explicit `full` marker. The 36 real-media items, 147 Windows-only items, one real external-service item, and one GPU-required item are additional dependency dimensions, not exclusions from L3.

The test suite is mostly self-contained, but its isolation is based on per-test fixtures, not xdist workers:

- The shared `api` and `studio` fixtures create a fresh SQLite file under pytest's per-test `tmp_path`, create the schema, and start/stop a FastAPI `TestClient` for that test. This avoids sharing application state, but repeats engine, schema, and app startup.
- Ten test modules directly construct `TestClient`/FastAPI applications or call `make_studio`; four directly call `Base.metadata.create_all`. The common fixtures account for additional per-test initialization.
- Twenty-one test modules mention fixed loopback addresses or ports. Tests also patch process-wide environment variables and create child processes, local HTTP fakes, threads, and SQLite write races.
- Twenty-one modules use thread/event/barrier/executor synchronization. Some tests intentionally contend on a shared SQLite writer inside their own temporary database.
- For these reasons xdist is not enabled. The current evidence does not prove safe worker-level isolation of ports, environment mutation, process ownership, and temporary paths. Each tier runs serially.

The post-correction Linux L3 run skipped 147 Windows operations. The table groups those actual skip outcomes by module; Windows CI is expected to execute these cases rather than skip them. Two non-Windows L3 skips are recorded separately in `TEST_REFACTOR_ACCEPTANCE.md`.

Three local-executor tests in `test_new_machine_setup.py` use runtime `pytest.skip` calls on non-Windows hosts rather than `skipif` decorators. They now carry explicit `windows_only` markers so the collected platform dimension matches the actual 147 Windows skips.

| Windows-only skips on Linux | Module |
| ---: | --- |
| 47 | `test_renderer_supervisor.py` |
| 15 | `test_managed_run_binding.py` |
| 15 | `test_package_entrypoints.py` |
| 14 | `test_bootstrap_entry.py` |
| 14 | `test_managed_startup.py` |
| 10 | `test_owned_process_group.py` |
| 7 | `test_installation_scripts.py` |
| 7 | `test_isolated_stage.py` |
| 7 | `test_service_validation.py` |
| 3 | `test_new_machine_setup.py` |
| 3 | `test_powershell_runtime_compat.py` |
| 2 | `test_director_setup_contract.py` |
| 1 | `test_image_managed_renderer.py` |
| 1 | `test_managed_series.py` |
| 1 | `test_process_probe.py` |

## Waits and retries found by source inventory

The source scan found 91 lines matching sleep, wait/join timeout, or timeout configuration across 29 test/support files. This includes real semantic waits and bounded safety limits; the count is an inventory, not a claim that all 91 are unnecessary. Examples include:

- `test_v13_factory.py::test_resource_slot_heartbeats_a_long_running_model` waited 16 seconds for the real background lease heartbeat. The refactor changes it to wait for the committed database update while retaining an assertion that production still requests a 15-second cadence.
- `test_comfy_handoff_journal.py::test_long_stage_holds_same_resource_until_exit` waits beyond its lease expiry to prove the heartbeat keeps ownership; its test-only lease is shortened from 2 to 1 second and the interval from 0.2 to 0.02 seconds.
- `test_gpu_release_confirmation.py::test_release_waits_longer_than_three_seconds_for_confirmed_unload` already uses a virtual clock and asserts the timeout boundary without sleeping for three seconds.
- Fixed waits in fake network timeout responses and child-process ownership tests model real timeout/process behavior and remain bounded. The test child processes that sleep for 60–120 seconds are terminated by the test; the suite does not wait for their full duration.

Forty-two call sites across 18 test modules generate synthetic FFmpeg videos. The helper returns immutable bytes, so it now caches up to 16 parameter sets for one pytest process. It still invokes FFmpeg for each distinct set and all media tests still decode, probe, or assemble the resulting real MP4 bytes.

## Slowest baseline test phases

Measured by `pytest --durations=50` on the unmodified 2,105-item collection. These are test phases, not estimates. The `profile` command can regenerate the same report on demand after future changes.

| Rank | Seconds | Phase | Test |
| ---: | ---: | --- | --- |
| 1 | 54.36 | call | `tests/test_v13_e2e.py::test_v13_full_preproduction_to_fake_h3_advisory_review_and_archive` |
| 2 | 50.46 | call | `tests/test_four_stage_reacceptance.py::test_r06_a_failed_chunk_is_kept_and_blocks_the_delivery` |
| 3 | 39.94 | call | `tests/test_four_stage_reacceptance.py::test_r02_a_partial_redo_keeps_the_other_scenes` |
| 4 | 33.27 | call | `tests/test_pipeline.py::test_volume_storyboard_draft_and_handoff` |
| 5 | 30.18 | call | `tests/test_storyboard.py::test_conflict_repair_creates_new_plan_then_rechecks_without_mutating_original` |
| 6 | 28.61 | call | `tests/test_storyboard.py::test_unknown_storyboard_result_blocks_exact_replay_before_agent_calls` |
| 7 | 28.54 | call | `tests/test_four_stage_reacceptance.py::test_r09_a_revoked_master_is_skipped_and_never_bound` |
| 8 | 28.41 | call | `tests/test_four_stage_reacceptance.py::test_r03_a_draft_blueprint_may_only_be_published_when_it_agrees` |
| 9 | 27.73 | call | `tests/test_four_stage_closeout.py::test_f06_a_scene_without_shots_blocks_the_handoff_by_name` |
| 10 | 27.73 | call | `tests/test_four_stage_reacceptance.py::test_r04_a_changed_shot_revision_produces_a_new_package` |
| 11 | 27.63 | call | `tests/test_four_stage_reacceptance.py::test_r05_two_confirmed_episodes_reach_the_volume_delivery` |
| 12 | 27.47 | call | `tests/test_four_stage_reacceptance.py::test_r05_the_package_manifest_keeps_the_episode_script_identity` |
| 13 | 27.43 | call | `tests/test_four_stage_reacceptance.py::test_r09_a_missing_material_is_a_notice_not_a_block` |
| 14 | 27.34 | call | `tests/test_four_stage_reacceptance.py::test_r05_a_non_contiguous_episode_order_is_refused` |
| 15 | 27.33 | call | `tests/test_four_stage_reacceptance.py::test_r05_a_superseded_scene_script_refuses_the_episode_delivery` |
| 16 | 27.31 | call | `tests/test_four_stage_reacceptance.py::test_r05_two_episodes_claiming_one_scene_are_refused` |
| 17 | 27.30 | call | `tests/test_four_stage_reacceptance.py::test_r05_a_missing_episode_refuses_the_delivery` |
| 18 | 27.30 | call | `tests/test_four_stage_reacceptance.py::test_r05_an_unconfirmed_scene_script_refuses_the_delivery` |
| 19 | 26.98 | call | `tests/test_v13_factory.py::test_sdk_voice_design_freeze_tts_asr_alignment_through_real_api` |
| 20 | 23.84 | call | `tests/test_storyboard.py::test_a_standing_conflict_keeps_the_storyboard_blocked` |
| 21 | 23.47 | call | `tests/test_storyboard.py::test_conflict_repair_cannot_change_locked_segment_timing` |
| 22 | 22.88 | call | `tests/test_story_continuity.py::test_verified_prior_end_state_replaces_free_text_facts` |
| 23 | 22.17 | call | `tests/test_storyboard.py::test_an_arbitration_citing_an_unknown_rule_releases_nothing` |
| 24 | 19.67 | call | `tests/test_sdk_role_process.py::test_conflicting_sdks_run_sequentially_in_different_processes_and_keep_mock_provenance` |
| 25 | 19.40 | call | `tests/test_v13_preparation.py::test_voice_design_freeze_dialogue_asr_alignment_subtitles` |
| 26 | 18.00 | call | `tests/test_v13_factory.py::test_resource_slot_heartbeats_a_long_running_model` |
| 27 | 17.89 | call | `tests/test_storyboard.py::test_crew_conflict_is_arbitrated_by_the_director_on_the_official_rules` |
| 28 | 17.36 | call | `tests/test_storyboard.py::test_the_server_binds_the_authoritative_issue_text` |
| 29 | 17.14 | call | `tests/test_storyboard.py::test_director_groups_editorial_beats_and_applies_timed_cut[4.3-True]` |
| 30 | 16.37 | call | `tests/test_story_preparation.py::test_outline_is_queued_claimed_resumed_and_merged_with_complete_source` |
| 31 | 16.11 | call | `tests/test_four_stage_closeout.py::test_f05_the_crew_lane_really_consults_the_persisted_consent` |
| 32 | 15.69 | call | `tests/test_film_crew.py::test_four_roles_handoff_fake_comfy_advisory_qc_invalidation_and_edl` |
| 33 | 14.76 | call | `tests/test_storyboard.py::test_storyboard_chain_generates_all_five_artifacts` |
| 34 | 14.74 | call | `tests/test_storyboard.py::test_the_arbiter_is_not_called_when_the_crew_agrees` |
| 35 | 14.69 | call | `tests/test_storyboard.py::test_storyboard_accepts_omitted_redundant_facts_and_optional_camera_details` |
| 36 | 14.61 | call | `tests/test_storyboard.py::test_director_is_instructed_to_include_materially_depicted_approved_props` |
| 37 | 14.36 | call | `tests/test_storyboard.py::test_storyboard_canonicalizes_character_asset_uuids_from_scriptwriter` |
| 38 | 14.29 | call | `tests/test_storyboard.py::test_continuity_reviewer_receives_the_checked_reference_set_policy` |
| 39 | 14.18 | call | `tests/test_storyboard.py::test_reference_set_policy_treats_the_voice_master_as_optional` |
| 40 | 14.17 | call | `tests/test_storyboard.py::test_director_groups_editorial_beats_and_applies_timed_cut[4.3-False]` |
| 41 | 14.16 | call | `tests/test_storyboard.py::test_storyboard_proposes_the_material_requirement_instead_of_blocking` |
| 42 | 14.16 | call | `tests/test_storyboard.py::test_chinese_locked_fact_translation_is_shown_and_kept_with_original` |
| 43 | 14.08 | call | `tests/test_storyboard.py::test_edit_intent_tool_permissions_and_coverage` |
| 44 | 14.05 | call | `tests/test_storyboard.py::test_series_accepts_frozen_edit_intent_and_rejects_invalid` |
| 45 | 14.01 | call | `tests/test_storyboard.py::test_storyboard_matches_character_view_suffix_to_approved_master` |
| 46 | 13.77 | call | `tests/test_story_continuity.py::test_forged_render_claim_is_overwritten_and_superseded_state_refuses` |
| 47 | 13.55 | call | `tests/test_story_continuity.py::test_changed_outline_section_invalidates_derived_end_state` |
| 48 | 13.38 | call | `tests/test_story_section_run.py::test_unknown_section_role_cannot_be_replaced_without_not_sent_proof` |
| 49 | 13.07 | call | `tests/test_story_section_run.py::test_only_the_selected_section_is_produced_and_the_context_is_verified` |
| 50 | 12.95 | call | `tests/test_story_section_run.py::test_section_reconcile_proves_waiting_resource_failure_was_not_sent` |

## Slowest modules

Measured by the pre-refresh full run's sum of pytest setup, call, and teardown durations. The first three modules account for about 44% of the 2,661.37 seconds of pytest phase time. These rows are module phase totals, not wall-clock estimates; they are not a profile of the newer `fe7d0d3` main.

| Rank | Seconds | Cases | Test module |
| ---: | ---: | ---: | --- |
| 1 | 405.805 | 18 | `test_four_stage_reacceptance.py` |
| 2 | 392.073 | 27 | `test_storyboard.py` |
| 3 | 380.507 | 112 | `test_native_capabilities.py` |
| 4 | 88.784 | 31 | `test_film_crew.py` |
| 5 | 81.038 | 19 | `test_v13_factory.py` |
| 6 | 73.986 | 26 | `test_review_input_evidence.py` |
| 7 | 63.694 | 13 | `test_character_binding.py` |
| 8 | 63.546 | 69 | `test_v13_preparation.py` |
| 9 | 60.968 | 12 | `test_sdk_role_process.py` |
| 10 | 60.044 | 17 | `test_pipeline.py` |
| 11 | 53.656 | 1 | `test_v13_e2e.py` |
| 12 | 52.319 | 16 | `test_four_stage_closeout.py` |
| 13 | 52.134 | 3 | `test_story_continuity.py` |
| 14 | 46.244 | 31 | `test_v13_adversarial.py` |
| 15 | 40.996 | 25 | `test_style_input_assessment.py` |
| 16 | 40.397 | 3 | `test_story_section_run.py` |
| 17 | 34.534 | 9 | `test_story_preparation.py` |
| 18 | 29.268 | 48 | `test_review_manifest_identity.py` |
| 19 | 25.679 | 28 | `test_llm_setup_contract.py` |
| 20 | 25.271 | 12 | `test_material_scene_safety.py` |

The new timings confirm that the largest L2 hotspot is `test_storyboard.py` (265.5 seconds in the L2 run). `test_native_capabilities.py` is also a large full-only module at 380.5 seconds; it remains in L3. The test profiler and runner emit this top-module list after future runs so changes remain measurable.
