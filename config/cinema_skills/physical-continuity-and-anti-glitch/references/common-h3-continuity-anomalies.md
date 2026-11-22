# Common H3 Video Generation Continuity Anomalies

## High-Signal Checks to Prevent Artifacts
- **Unanchored Entrances**: Avoid vague descriptions where a second performer suddenly appears mid-shot without walking in from off-frame or already being seated/standing in the established space.
- **Unmotivated Camera Orbit + Complex Action**: Combining fast 360-degree camera orbit with two-person prop handoffs frequently causes limb distortion and prop disappearance; flag conflicting motion vs staging demands if they break spatial logic.
- **Dialogue Overflow**: Verify every `beat.dialogue[].end_s` fits inside its assigned shot/cut interval under rule `H3_TIMING`.
