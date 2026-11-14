"""D4 回归：/journey 的第①阶段在已经有剧本时不得再说「还没有剧本」。

症状：导入剧本后，script 阶段的 summary 是「已有剧本「sample-script.md」，待整理大纲」，
而同一响应里的 blockers 仍是 SCRIPT_NOT_PREPARED。同一个响应自相矛盾，
界面据此给出「去创意与剧本」的自环动作。
"""

from tests.conftest import create_project


def _upload(client, project: str, name: str = "sample-script.md") -> dict:
    response = client.post(
        f"/api/v1/projects/{project}/documents",
        files={"file": (name, b"Scene one.\nLin: hello.\n", "text/markdown")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _script_stage(client, project: str) -> dict:
    journey = client.get(f"/api/v1/projects/{project}/journey")
    assert journey.status_code == 200, journey.text
    return {row["id"]: row for row in journey.json()["stages"]}["script"]


def test_an_imported_script_reports_the_outline_as_the_missing_piece(api):
    client, _, _ = api
    project = create_project(client)
    _upload(client, project)

    script = _script_stage(client, project)
    assert script["done"] is False, script
    assert script["summary"].startswith("已有剧本"), script
    codes = {row["code"] for row in script["blockers"]}
    assert "SCRIPT_OUTLINE_NOT_CONFIRMED" in codes, script
    assert "SCRIPT_NOT_PREPARED" not in codes, script
    # 阻塞原因与摘要说同一件事：大纲没确认。
    assert "大纲" in script["blockers"][0]["message"], script


def test_a_project_without_any_script_still_reports_a_missing_script(api):
    client, _, _ = api
    project = create_project(client)

    script = _script_stage(client, project)
    assert script["done"] is False, script
    assert {row["code"] for row in script["blockers"]} == {"SCRIPT_NOT_PREPARED"}, script
