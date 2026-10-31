"""DeepParse adapter and conversion into stable cluster records."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from deepparse import Drain, synth_masks
from deepparse.drain.masks_application import MaskApplier
from deepparse.masks_types import Mask
from deepparse.tokenize import tokenize

from .models import MaskDefinition, ParameterSlot, ParsedCluster, SourceEvent
from .template_matching import literal_pattern, slot_pattern

DEEPPARSE_REVISION = "b53c29b379be5ab834ff990154297ef8fea8d98a"
_PLACEHOLDER = re.compile(r"<VAR:([A-Za-z0-9_]+)>")


@dataclass(frozen=True)
class ClusterResult:
    clusters: list[ParsedCluster]
    masks: list[MaskDefinition]
    assignments: list[int]


class DeepParseClusterer:
    def __init__(
        self,
        *,
        system: str,
        sample_size: int = 50,
        samples_per_cluster: int = 5,
        depth: int = 5,
        similarity_threshold: float = 0.4,
    ) -> None:
        if not system.strip():
            raise ValueError("system must not be blank")
        if sample_size <= 0 or samples_per_cluster <= 0:
            raise ValueError("sample sizes must be positive")
        self.system = system
        self.sample_size = sample_size
        self.samples_per_cluster = samples_per_cluster
        self.depth = depth
        self.similarity_threshold = similarity_threshold

    def cluster(self, events: Sequence[SourceEvent]) -> ClusterResult:
        if not events:
            raise ValueError("Cannot cluster an empty event sequence")

        texts = [event.text for event in events]
        raw_masks = synth_masks(
            texts,
            sample_size=min(self.sample_size, len(texts)),
            mode="offline",
        )
        masks = [MaskDefinition.model_validate(mask) for mask in raw_masks]
        raw_mask_dicts = [mask.model_dump() for mask in masks]
        drain = Drain(depth=self.depth, similarity_threshold=self.similarity_threshold)
        drain.load_masks(raw_mask_dicts)
        assignments = drain.parse_with_ids(texts)

        grouped: dict[int, tuple[str, list[SourceEvent]]] = {}
        for event, (engine_cluster_id, template) in zip(events, assignments, strict=True):
            if engine_cluster_id not in grouped:
                grouped[engine_cluster_id] = (template, [])
            grouped[engine_cluster_id][1].append(event)

        mask_applier = MaskApplier([Mask.from_dict(mask) for mask in raw_mask_dicts])
        clusters = []
        for engine_cluster_id, (template, members) in grouped.items():
            typed_template = _restore_typed_placeholders(template, members, mask_applier)
            clusters.append(self._to_cluster(engine_cluster_id + 1, typed_template, members))
        clusters.sort(key=lambda cluster: cluster.cluster_id)
        return ClusterResult(
            clusters=clusters,
            masks=masks,
            assignments=[cluster_id for cluster_id, _ in assignments],
        )

    def _to_cluster(
        self,
        engine_cluster_id: int,
        template: str,
        members: list[SourceEvent],
    ) -> ParsedCluster:
        identity = f"{self.system}\0{template}".encode()
        stable_id = hashlib.sha256(identity).hexdigest()[:16]
        return ParsedCluster(
            cluster_id=f"cluster-{stable_id}",
            engine_cluster_id=engine_cluster_id,
            template=template,
            event_count=len(members),
            representative_events=_representative_events(members, self.samples_per_cluster),
            parameter_slots=_parameter_slots(template, members),
        )


def _representative_events(events: list[SourceEvent], limit: int) -> list[SourceEvent]:
    if len(events) <= limit:
        return events
    if limit == 1:
        return [events[0]]
    last = len(events) - 1
    indices = sorted({round(position * last / (limit - 1)) for position in range(limit)})
    return [events[index] for index in indices]


def _parameter_slots(template: str, events: list[SourceEvent]) -> list[ParameterSlot]:
    matches = list(_PLACEHOLDER.finditer(template))
    if not matches:
        return []

    counts: Counter[str] = Counter()
    slot_values: list[list[str]] = [[] for _ in matches]
    value_pattern = _template_value_pattern(template, matches)
    for event in events:
        match = value_pattern.fullmatch(event.text)
        if match:
            for index, value in enumerate(match.groups()):
                if value not in slot_values[index]:
                    slot_values[index].append(value)

    slots: list[ParameterSlot] = []
    for index, match in enumerate(matches, start=1):
        label = match.group(1)
        counts[label] += 1
        slots.append(
            ParameterSlot(
                slot_id=f"p{index}",
                label=label,
                placeholder=match.group(0),
                occurrence=counts[label],
                examples=slot_values[index - 1][:5],
            )
        )
    return slots


def _template_value_pattern(template: str, matches: list[re.Match[str]]) -> re.Pattern[str]:
    parts = [r"\s*"]
    cursor = 0
    for match in matches:
        parts.append(literal_pattern(template[cursor : match.start()]))
        parts.append(f"({slot_pattern(template, match)})")
        cursor = match.end()
    parts.extend([literal_pattern(template[cursor:]), r"\s*"])
    return re.compile("".join(parts))


def _restore_typed_placeholders(
    template: str,
    events: list[SourceEvent],
    mask_applier: MaskApplier,
) -> str:
    """Recover type labels that DeepParse intentionally hides in rendered templates."""
    template_tokens = template.split()
    masked_tokens = [tokenize(mask_applier.apply(event.text)) for event in events]
    restored: list[str] = []
    for index, template_token in enumerate(template_tokens):
        if template_token != "<*>":
            restored.append(template_token.replace("<*>", "<VAR:TEXT>"))
            continue
        labels = {
            match.group(1)
            for tokens in masked_tokens
            if index < len(tokens)
            if (match := re.fullmatch(r"<VAR:([A-Za-z0-9_]+)>", tokens[index]))
        }
        label = labels.pop() if len(labels) == 1 else "TEXT"
        restored.append(f"<VAR:{label}>")
    return " ".join(restored)
