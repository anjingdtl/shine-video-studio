"""A registered profile may offer more reference slots than a request binds.

The loader node of an unused slot keeps the graph's placeholder file name, and
the renderer rejects the prompt because that file does not exist.  The
submitted graph therefore drops the unused loader and the input that pointed
at it - deterministically, and only when nothing else still reads it.
"""

from __future__ import annotations

from shine.comfy.workflow import prune_unused_reference_nodes


def _graph(loaders: int) -> dict:
    graph = {
        "31": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": ["20", 0],
                "prompt": "",
                "ref_images.ref_image_0": ["40", 0],
            },
        },
        "40": {"class_type": "LoadImage", "inputs": {"image": "placeholder-1.png"}},
        "57": {"class_type": "SaveVideo", "inputs": {"video": ["31", 0]}},
    }
    for index in range(1, loaders):
        node_id = str(40 + index)
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": f"placeholder-{index + 1}.png"}}
        graph["31"]["inputs"][f"ref_images.ref_image_{index}"] = [node_id, 0]
    return graph


def test_unused_reference_loaders_leave_the_submitted_graph():
    graph = _graph(4)
    pruned = prune_unused_reference_nodes(graph, {"40", "41"})

    # The two bound loaders stay, the two unused ones leave.
    assert "40" in pruned and "41" in pruned
    assert "42" not in pruned and "43" not in pruned
    assert pruned["31"]["inputs"]["ref_images.ref_image_0"] == ["40", 0]
    assert pruned["31"]["inputs"]["ref_images.ref_image_1"] == ["41", 0]
    assert "ref_images.ref_image_2" not in pruned["31"]["inputs"]
    assert "ref_images.ref_image_3" not in pruned["31"]["inputs"]
    # The rest of the graph is untouched.
    assert pruned["57"]["inputs"]["video"] == ["31", 0]
    assert pruned["31"]["inputs"]["prompt"] == ""


def test_a_fully_bound_graph_is_not_changed():
    graph = _graph(3)
    pruned = prune_unused_reference_nodes(graph, {"40", "41", "42"})
    assert pruned == graph


def test_a_loader_that_something_else_reads_stays():
    graph = _graph(2)
    # A second consumer reads the same loader, so it cannot leave the graph.
    graph["58"] = {"class_type": "PreviewImage", "inputs": {"images": ["41", 0]}}
    pruned = prune_unused_reference_nodes(graph, {"40"})
    assert "41" in pruned
    assert "ref_images.ref_image_1" not in pruned["31"]["inputs"]
    assert pruned["58"]["inputs"]["images"] == ["41", 0]


def test_the_original_graph_is_not_mutated():
    graph = _graph(3)
    prune_unused_reference_nodes(graph, {"40"})
    assert "ref_images.ref_image_1" in graph["31"]["inputs"]
    assert "41" in graph
