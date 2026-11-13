from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from shine.agents.tools import AssetMatcher, AssetView, ProjectContext
from shine.schemas import (
    DialogueLine,
    ScriptBeat,
    ScriptEpisode,
    ScriptPlan,
    ScriptScene,
    ShotProposal,
    ShotProposalList,
    ShotSpec,
    StyleBible,
)
from shine.script_labels import PLANNER_SCENE_LABELS

SPEAKER_PATTERN = re.compile(r"^\s*([^\s：:]{1,20})\s*[：:]\s*(.+)$")
SCENE_PATTERN = re.compile(
    r"^\s*(?:场景|第[一二三四五六七八九十百0-9]+场|INT\.|EXT\.|内景|外景|SCENE\b)",
    re.IGNORECASE,
)
NON_SPEAKER_LABELS = set(PLANNER_SCENE_LABELS)


@dataclass
class PlanResult:
    plan: ScriptPlan
    planner: str
    warnings: list[str]


def _split_scenes(text: str) -> list[tuple[str, list[str]]]:
    lines = [line.strip() for line in text.splitlines()]
    blocks: list[tuple[str, list[str]]] = []
    current_name = ""
    current: list[str] = []
    for line in lines:
        if not line:
            continue
        if SCENE_PATTERN.match(line) and len(line) <= 120:
            if current:
                blocks.append((current_name or f"场景 {len(blocks) + 1}", current))
            current_name = line[:160]
            current = []
            continue
        current.append(line)
    if current:
        blocks.append((current_name or f"场景 {len(blocks) + 1}", current))
    if not blocks:
        paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", text) if paragraph.strip()]
        for index in range(0, len(paragraphs), 3):
            chunk = paragraphs[index : index + 3]
            if chunk:
                blocks.append((f"场景 {len(blocks) + 1}", chunk))
    return blocks


def _characters_in(lines: list[str]) -> list[str]:
    characters: list[str] = []
    for line in lines:
        match = SPEAKER_PATTERN.match(line)
        if not match or match.group(1) in NON_SPEAKER_LABELS:
            continue
        name = match.group(1)[:80]
        if name not in characters:
            characters.append(name)
    return characters


class HeuristicScriptPlanner:
    """Deterministic, offline story-structure pass. It is a real planner, not a fixture."""

    name = "heuristic"

    def plan(self, text: str, title: str = "") -> PlanResult:
        scenes: list[ScriptScene] = []
        all_characters: list[str] = []
        for scene_name, lines in _split_scenes(text):
            beats: list[ScriptBeat] = []
            for line in lines[:30]:
                match = SPEAKER_PATTERN.match(line)
                if match and match.group(1) not in NON_SPEAKER_LABELS:
                    characters = [match.group(1)]
                    summary = line
                else:
                    characters = []
                    summary = line
                if summary and len(summary) >= 2:
                    beats.append(ScriptBeat(summary=summary[:1000], characters=characters))
                for name in characters:
                    if name not in all_characters:
                        all_characters.append(name)
            if not beats:
                continue
            location = ""
            if "：" in scene_name or ":" in scene_name:
                location = scene_name.split("：", 1)[-1].split(":", 1)[-1][:300]
            scenes.append(ScriptScene(name=scene_name, location=location, beats=beats[:30]))
        if not scenes:
            scenes = [ScriptScene(name="场景 1", beats=[ScriptBeat(summary=text[:1000], characters=[])])]
        plan = ScriptPlan(
            title=title,
            episodes=[
                ScriptEpisode(
                    name="第一集",
                    scenes=scenes,
                )
            ],
        )
        return PlanResult(plan=plan, planner=self.name, warnings=[])


class LLMScriptPlanner:
    name = "llm"

    def __init__(self, model):
        self._model = model

    def plan(self, text: str, title: str = "") -> PlanResult:
        schema = ScriptPlan.model_json_schema()
        response = self._model.complete_json(
            system=(
                "You are Shine's ScriptPlanner. Split the supplied script into episodes, scenes, and story beats. "
                "Return schema-valid JSON only. Never invent user assets, approve assets, or change locked story "
                "facts. Keep character names exactly as written in the script."
            ),
            user=f"Title: {title}\nScript:\n{text}",
            schema=schema,
        )
        plan = ScriptPlan.model_validate(response)
        if not plan.episodes:
            raise ValueError("The model returned a script plan without episodes")
        return PlanResult(plan=plan, planner=self.name, warnings=[])


def _estimate_duration(beat_text: str, line_count: int) -> float:
    estimate = 2.5 + line_count * 2.0 + min(4.0, len(beat_text) / 40)
    return max(4.0, min(15.0, round(estimate * 2) / 2))


def _dialogue_from_beat(text: str, characters: list[str], duration: float) -> list[DialogueLine]:
    lines: list[DialogueLine] = []
    match = SPEAKER_PATTERN.match(text)
    if not match or match.group(1) in NON_SPEAKER_LABELS:
        return lines
    speaker = match.group(1)
    content = match.group(2).strip()
    if speaker not in characters or not content:
        return lines
    end = min(duration - 0.2, max(2.0, len(content) / 4.5 + 0.8))
    lines.append(DialogueLine(speaker_id=speaker, text=content[:1000], start_s=0.4, end_s=round(end, 2)))
    return lines


class ShotPlanner:
    """Turns a script plan into ShotSpec proposals bounded by approved user assets."""

    def __init__(self, model=None, matcher: AssetMatcher | None = None):
        self._model = model
        self._matcher = matcher or AssetMatcher()

    def propose(
        self,
        plan: ScriptPlan,
        *,
        style_version_id: str,
        style: StyleBible | None,
        context: ProjectContext,
        max_shots: int = 6,
    ) -> tuple[list[ShotProposal], str, list[str]]:
        if self._model is not None:
            proposals, warnings = self._propose_with_model(
                plan, style_version_id=style_version_id, context=context, max_shots=max_shots
            )
            return proposals, "llm", warnings
        return self._propose_heuristic(
            plan, style_version_id=style_version_id, style=style, context=context, max_shots=max_shots
        )

    def _propose_with_model(
        self, plan: ScriptPlan, *, style_version_id: str, context: ProjectContext, max_shots: int
    ) -> tuple[list[ShotProposal], list[str]]:
        schema = ShotProposalList.model_json_schema()
        response = self._model.complete_json(
            system=(
                "You are Shine's ShotPlanner. Convert the approved script plan into ShotSpec proposals using only "
                f"the fixed style version {style_version_id} and the listed user-approved assets. Do not create, "
                "replace, or approve assets. Do not change locked story facts. Return schema-valid JSON."
            ),
            user=(
                f"Maximum shots: {max_shots}\n"
                f"Project context:\n{context.safe_prompt_view()}\n"
                f"Script plan:\n{plan.model_dump_json()}"
            ),
            schema=schema,
        )
        proposal_list = ShotProposalList.model_validate(response)
        warnings: list[str] = []
        approved = {asset.id: asset for asset in context.usable_assets()}
        cleaned: list[ShotProposal] = []
        for proposal in proposal_list.shots[:max_shots]:
            spec = proposal.spec
            if spec.style_version_id and spec.style_version_id != style_version_id:
                raise ValueError("The model selected a style version other than the frozen project style")
            update = {"style_version_id": style_version_id}
            unknown = [asset_id for asset_id in spec.required_asset_ids if asset_id not in approved]
            if unknown:
                warnings.append("Unapproved or unknown asset ids were removed from a proposal")
                update["required_asset_ids"] = [
                    asset_id for asset_id in spec.required_asset_ids if asset_id in approved
                ]
            cleaned.append(ShotProposal(name=proposal.name, spec=spec.model_copy(update=update)))
        return cleaned, warnings

    def _propose_heuristic(
        self,
        plan: ScriptPlan,
        *,
        style_version_id: str,
        style: StyleBible | None,
        context: ProjectContext,
        max_shots: int,
    ) -> tuple[list[ShotProposal], str, list[str]]:
        warnings: list[str] = []
        proposals: list[ShotProposal] = []
        usable = context.usable_assets()
        for episode in plan.episodes:
            for scene in episode.scenes:
                for beat in scene.beats:
                    if len(proposals) >= max_shots:
                        break
                    speaker_match = SPEAKER_PATTERN.match(beat.summary)
                    speaker = (
                        speaker_match.group(1)
                        if speaker_match and speaker_match.group(1) not in NON_SPEAKER_LABELS
                        else None
                    )
                    detected = [name for name in beat.characters if name in _character_names(usable)] or (
                        self._matcher.characters_in(beat.summary, usable)
                    )
                    characters = list(dict.fromkeys(([speaker] if speaker else []) + detected))[:20]
                    asset_ids = self._matcher.match(beat.summary, usable, characters)
                    dialogue = _dialogue_from_beat(beat.summary, characters, _estimate_duration(beat.summary, 1))
                    duration = _estimate_duration(beat.summary, len(dialogue))
                    spec = ShotSpec(
                        story_beat=beat.summary,
                        duration_target_s=duration,
                        shot_priority="B",
                        style_version_id=style_version_id,
                        camera={"shot_size": "medium", "movement": "static", "axis_rule": "preserve"},
                        characters=characters,
                        required_asset_ids=asset_ids,
                        dialogue=dialogue,
                        sound={
                            "environment": scene.location or "local interior",
                            "music_policy": "low_or_none",
                        },
                        locked_story_facts=[],
                    )
                    if len(asset_ids) > 12:
                        warnings.append("A shot exceeded the 12-file reference cap and was trimmed")
                    proposals.append(
                        ShotProposal(name=f"{scene.name}-{len(proposals) + 1}"[:160], spec=spec)
                    )
                if len(proposals) >= max_shots:
                    break
            if len(proposals) >= max_shots:
                break
        if not proposals:
            warnings.append("No story beats were available for shot planning")
        if style is None:
            warnings.append("The frozen Style Bible snapshot was not available to the planner")
        return proposals, "heuristic", warnings


def _character_names(assets: tuple[AssetView, ...]) -> set[str]:
    names: set[str] = set()
    for asset in assets:
        for value in (asset.entity_id, asset.role, *asset.tags):
            if value:
                names.add(value)
    return names


def build_context(
    *,
    project_id: str,
    title: str,
    language: str,
    style_version_id: str,
    style: StyleBible | None,
    assets: list[AssetView],
    shots: list[dict[str, Any]] | None = None,
) -> ProjectContext:
    return ProjectContext(
        project_id=project_id,
        title=title,
        language=language,
        style_version_id=style_version_id,
        style=style,
        assets=tuple(assets),
        shot_summaries=tuple(shots or []),
    )
