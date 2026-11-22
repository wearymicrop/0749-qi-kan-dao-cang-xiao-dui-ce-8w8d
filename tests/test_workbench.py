def test_workbench_exposes_actions_and_both_samples(client):
    response = client.get('/')
    assert response.status_code == 200
    assert all(text in response.text for text in ['期刊到藏校对册', '证据寄送', '归档本次勾稽', '十一月补刊清单', '验收清册'])
    assert client.get('/static/desk.js').status_code == 200


def test_workbench_exposes_signing_and_revision_controls(client):
    response = client.get('/')
    assert all(text in response.text for text in ['交班签收', '供应商补录', '签收本版', '登记补录并生成修订版', '修订对照'])
    assert all(marker in response.text for marker in ['change-labels', 'supplement-kinds'])
    assert 'revision.js' in client.get('/static/desk.js').text
    assert client.get('/static/revision.js').status_code == 200
