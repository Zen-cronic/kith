import json

from household.fixtures import FIXTURES_DIR, FixtureStore, RequestFixture, adhoc_request
from household.providers.fake import SCHEMA_BY_ROLE

REQUIRED = {"ama-dental-cob", "kofi-allowance-8", "kofi-allowance-40", "daniel-payment-450", "mei-email-teacher", "ama-for-kofi-tuition"}


def test_request_fixtures_load_with_expectations() -> None:
    store = FixtureStore()
    requests = {r.id: r for r in store.requests()}
    assert REQUIRED <= set(requests)
    household = store.household("demo")
    for req in requests.values():
        assert req.request.strip() and req.household_id == "demo"
        assert household.member(req.actor_member_id) is not None
        assert req.expected.kind in {"in-scope", "out-of-scope"}
        for member_id in req.expected.approval_by:
            assert household.member(member_id) is not None
    assert store.request("kofi-allowance-8").actor_member_id == "kofi"
    assert store.request_from(str(FIXTURES_DIR / "requests" / "kofi-allowance-8.json"), "mei").actor_member_id == "mei"
    assert store.request_from("take $5 please", "kofi") == adhoc_request("take $5 please", "kofi")


def test_canned_outputs_validate_against_their_node_schema() -> None:
    store = FixtureStore()
    files = store.canned_files()
    assert files
    for path in files:
        request_id, node, *rest = path.stem.split(".")
        assert node in SCHEMA_BY_ROLE, path.name
        assert store.request(request_id)  # every canned file belongs to a request fixture
        raw = json.loads(path.read_text(encoding="utf-8"))
        if node == "authority":
            assert set(raw) <= {"verdict", "decisions"} and raw.get("verdict", "proceed") in {"proceed", "revise", "stop"}
            continue
        SCHEMA_BY_ROLE[node].model_validate(raw)
        run = int(rest[0]) if rest else 1
        assert store.canned(request_id, node, run) == raw
    assert store.canned("daniel-payment-450", "planner", 2) is not None
    assert store.canned("kofi-allowance-8", "planner", 2) is None


def test_document_fixtures_still_load_for_later_intake_packets() -> None:
    docs = FixtureStore().list()
    assert len(docs) >= 3 and all(doc.text.strip() for doc in docs)


def test_request_fixture_round_trips_from_raw() -> None:
    raw = json.loads((FIXTURES_DIR / "requests" / "daniel-payment-450.json").read_text(encoding="utf-8"))
    fixture = RequestFixture.from_raw(raw)
    assert fixture.expected.approval_by == ("daniel",) and fixture.expected.allow == ("payment:transfer",)
    assert "revise" in fixture.tags
