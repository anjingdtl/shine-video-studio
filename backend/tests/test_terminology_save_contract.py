"""人名对照表必须真的能存进去（专业模式术语面板的保存链路）。

根因链：
  backend/shine/main.py 的 create_terminology 把请求体里的 body.terms
  （Pydantic 模型 TerminologyTerm 的列表）原样传给
  backend/shine/services.py 的 save_project_terminology，再交给
  backend/shine/quality/terminology.py 的 parse_table。
  parse_table 只认 dict（第 92 行 isinstance(row, dict) 否则抛
  TERMINOLOGY_ROW_INVALID），所以只要填了任一词条就 422，
  留空（空列表）却能存成 0 词条——于是 ASR 的专名提示永远拿不到词条。

这条测试走真实 HTTP 端点（POST /api/v1/projects/{id}/terminology），
不绕过界面也不绕过解析器：填了词条必须存进去，并且存进去的词条
必须能被 ASR 提示读到。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from .conftest import create_project


def test_a_filled_terminology_table_is_stored(api):
    """填了词条就必须存得进去，且存进去的词条能被 ASR 提示读到。"""
    from shine.services import project_terminology_hint

    client, factory, settings = api
    project_id = create_project(client, "人名对照表")

    response = client.post(f"/api/v1/projects/{project_id}/terminology", json={
        "terms": [
            {"canonical": "周砚", "aliases": ["周烨"]},
            {"canonical": "林知远"},
        ],
        "created_by": "operator",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 1
    assert body["terms"] == [
        {"canonical": "周砚", "aliases": ["周烨"]},
        {"canonical": "林知远", "aliases": []},
    ]

    # 存进去的词条必须真的到达 ASR 提示：否则界面上「保存成功」是假的。
    with factory() as db:
        hint = project_terminology_hint(db, project_id)
    assert "周砚" in hint
    assert "林知远" in hint


def test_an_empty_terminology_table_is_still_refused_as_a_wrong_row(api):
    """空词条仍然是无效行（防御路径不许被放宽成「什么都行」）。"""
    client, factory, settings = api
    project_id = create_project(client, "空词条")

    response = client.post(f"/api/v1/projects/{project_id}/terminology", json={
        "terms": [{"canonical": "   "}],
        "created_by": "operator",
    })
    assert response.status_code == 422, response.text
    assert "TERMINOLOGY_TERM_EMPTY" in response.text


def test_the_service_accepts_model_rows_and_refuses_a_row_it_cannot_read(api):
    """防御层自身必须有效：模型行被归一化，无法读取的行具名拒绝。

    上一条用例只经由 HTTP 端点走 main.py 的转换，锁不住 services.py 的
    防御本身。这一条直接调用服务层：把模型实例传进去必须存得进去，
    传一个既不是 dict 也不是模型的对象必须以具名错误码拒绝。
    """
    from shine.schemas import TerminologyTerm
    from shine.services import save_project_terminology

    client, factory, settings = api
    project_id = create_project(client, "术语防御")

    with factory() as db:
        from shine.models import Project

        project = db.get(Project, project_id)
        row = save_project_terminology(
            db, project,
            terms=[TerminologyTerm(canonical="周砚", aliases=["周烨"])],
            created_by="operator",
        )
        db.commit()
        assert row.terms == [{"canonical": "周砚", "aliases": ["周烨"]}]

        # 一行既不是 dict 也不是模型：必须具名拒绝，不许静默变成错误提示。
        with pytest.raises(HTTPException) as caught:
            save_project_terminology(
                db, project, terms=[object()], created_by="operator")
        assert caught.value.status_code == 422
        assert caught.value.detail == "TERMINOLOGY_ROW_INVALID"


def test_the_table_is_stored_as_a_new_revision(api):
    """每次保存都是新版本，早先的版本不被改写。"""
    client, factory, settings = api
    project_id = create_project(client, "术语版本")

    first = client.post(f"/api/v1/projects/{project_id}/terminology", json={
        "terms": [{"canonical": "周砚"}],
        "created_by": "operator",
    })
    assert first.status_code == 200, first.text
    assert first.json()["revision"] == 1

    second = client.post(f"/api/v1/projects/{project_id}/terminology", json={
        "terms": [{"canonical": "周砚"}, {"canonical": "林知远"}],
        "created_by": "operator",
    })
    assert second.status_code == 200, second.text
    assert second.json()["revision"] == 2

    listed = client.get(f"/api/v1/projects/{project_id}/terminology")
    assert listed.status_code == 200
    rows = listed.json()
    assert [row["revision"] for row in rows] == [2, 1]
    assert rows[1]["terms"] == [{"canonical": "周砚", "aliases": []}]
