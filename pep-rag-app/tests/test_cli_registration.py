from __future__ import annotations

import peptalk.__main__ as cli


def test_ask_and_serve_are_registered():
    parser = cli.build_parser()
    args = parser.parse_args(["ask", "What is PEP 572?", "--mode", "bm25", "-k", "3"])
    assert args.command == "ask"
    assert (args.question, args.mode, args.k) == ("What is PEP 572?", "bm25", 3)
    assert callable(args.func)

    args = parser.parse_args(["serve"])
    assert args.command == "serve" and args.dense is False
    assert parser.parse_args(["serve", "--dense"]).dense is True


def test_serve_passes_dense_flag_to_serve_index(monkeypatch):
    calls = []

    def fake_serve_index(settings, dense=False):
        calls.append(dense)

    import peptalk.web as web

    monkeypatch.setattr(web, "serve_index", fake_serve_index)
    assert cli.main(["serve", "--dense"]) == 0
    assert cli.main(["serve"]) == 0
    assert calls == [True, False]


def test_serve_rejects_bad_port_before_loading_index(monkeypatch, capsys):
    import peptalk.web as web

    def fail_serve_index(settings, dense=False):
        raise AssertionError("serve_index must not run with a bad PORT")

    monkeypatch.setattr(web, "serve_index", fail_serve_index)
    monkeypatch.setenv("PORT", "abc")
    assert cli.main(["serve"]) == 2
    err = capsys.readouterr().err
    assert "PORT must be an integer" in err
    assert "'abc'" in err
