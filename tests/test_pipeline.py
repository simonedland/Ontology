"""Semantic tests: the demo's planted scenarios must be found, and TGO invariants must hold."""
from __future__ import annotations

import json
import re

import pytest

from tgo import identifiers as ids
from tgo import media as M
from tgo import validate
from tgo.analysis.identity import Resolver
from tgo.demo.synth import CENTRAL, GARDEN, MARK, MD2, OLA, REGIONAL, THROWAWAYS, TRON_WALLET
from tgo.graph import Graph


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from tgo.demo.synth import generate
    from tgo.pipeline import build_graph

    d = generate(tmp_path_factory.mktemp("tgo") / "input")
    g, report = build_graph(d, log=lambda *a: None)
    return g, report


# ------------------------------------------------------------------ units
def test_identifier_checksums():
    found = {f.label: f for f in ids.extract(
        f"IBAN GB82 WEST 1234 5698 7654 32, ETH 0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed, {TRON_WALLET}")}
    assert found["IBAN"].validation == "iban-mod97"
    assert {f.validation for f in ids.extract("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")} == {"eip55"}
    # one flipped case breaks EIP-55 -> rejected, and a bad IBAN digit -> rejected
    assert not ids.extract("0x5AAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")
    assert not [f for f in ids.extract("GB83 WEST 1234 5698 7654 32") if f.label == "IBAN"]
    assert ids.keccak256(b"").hex().startswith("c5d2460186f7233c")


def test_document_classification_order():
    vid = {"_": "DocumentAttributeVideo", "w": 1, "h": 1, "duration": 1}
    assert M.classify_document("video/webm", [vid, {"_": "DocumentAttributeSticker"}]) == "Sticker"
    assert M.classify_document("video/mp4", [vid, {"_": "DocumentAttributeAnimated"}]) == "Animation"
    assert M.classify_document("video/mp4", [dict(vid, round_message=True)]) == "RoundVideo"
    assert M.classify_document("audio/ogg", [{"_": "DocumentAttributeAudio", "voice": True}]) == "VoiceNote"
    assert M.classify_document("application/pdf", []) == "Document"


def test_waveform_roundtrip():
    samples = [i / 31 for i in range(32)] * 3
    out = M.decode_waveform(M.encode_waveform(samples))
    assert [round(x * 31) for x in out[:len(samples)]] == [round(x * 31) for x in samples]


def test_media_area_fragment():
    sel = M.media_area_selector(50, 50, 20, 10)
    assert sel["FragmentSelector"] == "xywh=percent:40.00,45.00,20.00,10.00"
    assert "SvgSelector" in M.media_area_selector(50, 50, 20, 10, rotation=15)


def test_resolver_cannot_link_and_explode():
    g = Graph()
    for u in ("user:1", "user:2", "user:3"):
        g.node(u, "HumanAccount")
    r = Resolver(g)
    r.decide("user:1", "user:3", "negative", "analyst:t")
    assert r.decide("user:1", "user:2", "positive", "analyst:t")["merged"]
    blocked = r.decide("user:2", "user:3", "positive", "analyst:t")   # would join 1 and 3 transitively
    assert not blocked["merged"]
    pc = r.cluster_of("user:1")
    assert r.members(pc) == {"user:1", "user:2"}
    assert r.explode(pc) == 2 and r.cluster_of("user:1") is None


# ------------------------------------------------------------- scenarios
def test_multi_source_merge(built):
    g, _ = built
    # the album message exists in both the MTProto dump and the Desktop export -> one node
    msg = g.nodes[f"channel:1600300400/msg:88213"]
    assert len({s.split(":")[1] for s in msg.sources}) == 2
    # same JPEG bytes from both sources -> one sha256-keyed file
    jpgs = [n for n in g.by_label("File") if n.props.get("fileName") == "IMG_8841.JPG"]
    assert len(jpgs) == 1 and jpgs[0].key.startswith("file:")


def test_contact_names_do_not_leak(built):
    g, _ = built
    assert g.nodes[f"user:{MARK}"].props["label"] == "Mark Dunmore"     # not the collector's "Mark (harbor)"
    assert g.nodes[f"user:{MARK}"].props["collectorDisplayName"] == "Mark (harbor)"   # kept, but separately
    assert any(n.props.get("label") == "Mark (harbor)" for n in g.by_label("ContactAnnotation"))


def test_coordination_cluster_is_exactly_the_franchise(built):
    g, _ = built
    clusters = g.by_label("CoordinationCluster")
    assert len(clusters) == 1
    members = set(clusters[0].props["members"])
    assert members == {f"channel:{CENTRAL}"} | {f"channel:{c}" for c in REGIONAL}
    assert f"channel:{GARDEN}" not in members                       # organic single late forward
    assert g.by_type("SYNC_POST") and g.by_type("SAME_CREATION_DATE")
    assert "originator" in g.nodes[f"channel:{CENTRAL}"].props["networkRoles"]


def test_identity_candidates_and_judgements(built):
    g, _ = built
    cand = {(e.src, e.dst): e for e in g.by_type("CANDIDATE_SAME_PERSON")}
    strong = cand[(f"user:{MARK}", f"user:{MD2}")]
    assert strong.conf > 0.9 and strong.props["judgement"] == "positive"
    assert {"shared_device_serial", "same_personal_channel", "shared_avatar_hash"} <= set(strong.props["features"])
    weak = cand[(f"user:{MARK}", f"user:{OLA}")]
    assert weak.conf < 0.6 and weak.props["judgement"] == "negative"
    # stock avatar shared by four throwaways is IDF-downweighted below threshold
    assert not any({e.src, e.dst} <= {f"user:{u}" for u in THROWAWAYS} for e in cand.values())
    pcs = g.by_label("PersonCluster")
    assert len(pcs) == 1
    assert {e.src for e in g.in_edges(pcs[0].key, "OPERATED_BY")} == {f"user:{MARK}", f"user:{MD2}"}
    assert not g.by_label("Person") or all(n.props.get("mintedBy") for n in g.by_label("Person"))


def test_media_extraction(built):
    g, _ = built
    assert g.by_type("CAPTURED_WITH") and all(
        g.nodes[e.src].props["sendMode"] == "DocumentMode" for e in g.by_type("CAPTURED_WITH"))
    assert len({e.src for e in g.by_type("CAPTURED_WITH")}) == 2      # two files, one camera
    assert g.by_type("SAME_DOCUMENT_LINEAGE")                         # same xmpMM:DocumentID, other bytes
    tr = g.by_label("Transcript")[0]
    assert TRON_WALLET in tr.props["text"]
    voice = next(e.src for e in g.by_type("HAS_TRANSCRIPT"))
    assert {g.nodes[e.dst].props["value"] for e in g.out_edges(voice, "CONTAINS_IDENTIFIER")} >= {TRON_WALLET}
    assert any(n.props.get("name") == "Kirkenes" for n in g.by_label("ExtractedEntity"))
    assert any(n.props.get("waveform") for n in g.by_label("VoiceNote"))


def test_typed_gaps_and_privacy(built):
    g, _ = built
    md2 = g.nodes[f"user:{MD2}"]
    assert md2.fields["profilePhoto"]["visibility"] == "hidden_by_privacy"
    assert any(n.props.get("fileStatus") == "excluded_by_export_settings" for n in g.nodes.values())
    assert g.nodes[f"user:{MARK}"].props["hiddenGiftSenders"] == 1
    assert all(e.props.get("observed_by") for e in g.edges.values() if e.props.get("scope") == "collector_only")


def test_layer230_and_social_edges(built):
    g, _ = built
    assert len(list(g.in_edges("community:4200000001", "MEMBER_OF_COMMUNITY"))) == 3
    assert g.by_type("REPOSTED") and g.by_type("GAVE_GIFT_TO") and g.by_type("NAME_HISTORY_LINK")
    assert [e.props["method"] for e in g.by_type("OWNED_BY")] == ["short_name _by_<bot> suffix"]


def test_guardrails(built):
    g, _ = built
    f = validate.validate(g)
    assert validate.summary(f)["violation"] == 0, [x for x in f if x.severity == "violation"]
    assert any(x.rule == "R6-art9-membership" for x in f)
    # injecting a forbidden edge / an inferred edge without provenance is caught
    g2 = Graph()
    g2.edge("SAME_AS", "user:1", "user:2")
    g2.edge("NEAR_DUPLICATE_OF", "file:a", "file:b", conf=1.0)
    rules = {x.rule for x in validate.validate(g2) if x.severity == "violation"}
    assert {"R2-no-sameas-no-biometrics", "R1-derived-provenance"} <= rules


def test_exports(tmp_path, built):
    from tgo import export as X

    g, _ = built
    X.write_turtle(g, tmp_path / "a.ttl")
    ttl = (tmp_path / "a.ttl").read_text(encoding="utf-8")
    assert not re.search(r"\sowl:sameAs\s+<", ttl)            # never used as a predicate
    assert "tg:Assertion" in ttl and "org:Membership" in ttl
    X.write_ftm(g, tmp_path / "f.jsonl")
    schemata = {json.loads(l)["schema"] for l in (tmp_path / "f.jsonl").read_text(encoding="utf-8").splitlines()}
    assert {"UserAccount", "Organization", "Similar", "Membership", "CryptoWallet", "BankAccount"} <= schemata
    X.write_stix(g, tmp_path / "s.json")
    types = {o["type"] for o in json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["objects"]}
    assert {"user-account", "channel", "cryptocurrency-wallet", "relationship"} <= types


def test_forward_header_never_overwrites_original(tmp_path):
    import json as _j

    from tgo.ingest.mtproto import ingest

    d = tmp_path / "mt"
    d.mkdir()
    (d / "collector.json").write_text(_j.dumps({"collector": {"_": "User", "id": 1, "first_name": "C"}}))
    (d / "entities.jsonl").write_text("")
    orig = {"_": "Message", "id": 5, "peer_id": {"_": "PeerChannel", "channel_id": 9}, "date": 1000, "message": "x", "post": True}
    fwd = {"_": "Message", "id": 6, "peer_id": {"_": "PeerChannel", "channel_id": 8}, "date": 5000, "message": "x", "post": True,
           "fwd_from": {"_": "MessageFwdHeader", "from_id": {"_": "PeerChannel", "channel_id": 9}, "date": 999, "channel_post": 5}}
    (d / "messages.jsonl").write_text(chr(10).join(_j.dumps(m) for m in (orig, fwd)))
    g = Graph()
    ingest(g, d)
    assert g.nodes["channel:9/msg:5"].props["date"] == 1000


def test_coordination_null_model_rejects_popular_url_noise():
    import random as _r

    from tgo.analysis.coordination import Share, detect_groups

    rnd = _r.Random(3)
    shares = []
    for a in range(40):                         # background: 40 accounts posting 40 popular URLs at random
        for i in range(300):
            shares.append(Share(f"url:{rnd.randrange(40)}", "url", f"acct{a}", f"m{a}-{i}", rnd.randrange(30 * 86400)))
    for k in range(30):                         # planted: 5 accounts share 30 texts within seconds
        for j in range(5):
            shares.append(Share(f"text:{k}", "text", f"coord{j}", f"c{j}-{k}", 10 * 86400 + k * 3600 + j))
    plain = detect_groups(shares, 60, 2, max_p=None)
    tested = detect_groups(shares, 60, 2)
    assert any(a.startswith("acct") for pair in plain for a in pair)        # plain CooRTweet: false positives
    assert {a for pair in tested for a in pair} == {f"coord{j}" for j in range(5)}
