#!/usr/bin/env python3
"""Prepare the transcript: whole sentences, then Chinese translations for the parts you use.

Commands:
  prep_transcript.py merge whisper.json -o transcript.json
      Whisper often splits at pauses, not at sentence ends. This joins fragments until the
      text ends with . ? ! 。？！… , or the gap to the next fragment exceeds --gap seconds,
      or the sentence reaches --max-sec. Sentence boundaries decide where cuts may land.

  prep_transcript.py todo transcript.json --plan plan.json -o todo.tsv
      Lists only the sentences inside the plan's source clips that have no "zh" yet, as a
      TSV (id, start, end, text, zh). Translate just these instead of the whole interview.

  prep_transcript.py apply transcript.json todo.tsv
      Writes the filled "zh" column back into transcript.json.

Translation rules (whoever fills the zh column, a person or a model):
  keep names, numbers and units exactly as spoken; translate meaning, not word order;
  one sentence in, one sentence out; mark anything unclear with [?] instead of guessing.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

END = re.compile(r"[.?!。？！…]['\"”’)]?\s*$")


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["segments"] if isinstance(data, dict) else data


def save(path, segs):
    Path(path).write_text(json.dumps({"segments": segs}, ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_merge(a):
    raw = load(a.input)
    out, cur = [], None
    for s in raw:
        text = s.get("text", "").strip()
        if not text:
            continue
        if cur is None:
            cur = {"start": float(s["start"]), "end": float(s["end"]), "text": text}
        else:
            gap = float(s["start"]) - cur["end"]
            if gap > a.gap or cur["end"] - cur["start"] > a.max_sec:
                out.append(cur)
                cur = {"start": float(s["start"]), "end": float(s["end"]), "text": text}
            else:
                sep = "" if re.search(r"[\u4e00-\u9fff]$", cur["text"]) else " "
                cur["text"] += sep + text
                cur["end"] = float(s["end"])
        if END.search(cur["text"]):
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    for i, s in enumerate(out):
        s["id"] = i
        s["start"], s["end"] = round(s["start"], 3), round(s["end"], 3)
    save(a.output, out)
    unfinished = sum(1 for s in out if not END.search(s["text"]))
    print(f"{len(raw)} fragments -> {len(out)} sentences: {a.output}")
    if unfinished:
        print(f"WARN {unfinished} sentences do not end with punctuation (split by pause or length); "
              "check them before cutting there")


def cmd_todo(a):
    segs = load(a.transcript)
    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    ranges = [(c["start"], c["end"]) for c in plan["clips"] if c.get("type", "source") == "source"]
    rows = []
    for i, s in enumerate(segs):
        if (s.get("zh") or "").strip():
            continue
        if any(min(e, s["end"]) - max(st, s["start"]) > 0.2 for st, e in ranges):
            rows.append([s.get("id", i), s["start"], s["end"], s["text"], ""])
    with open(a.output, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["id", "start", "end", "text", "zh"])
        w.writerows(rows)
    print(f"{len(rows)} sentences need Chinese: {a.output}")


def cmd_apply(a):
    segs = load(a.transcript)
    by_id = {s.get("id", i): s for i, s in enumerate(segs)}
    n = 0
    with open(a.tsv, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            zh = (row.get("zh") or "").strip()
            if not zh:
                continue
            sid = int(row["id"])
            if sid not in by_id:
                sys.exit(f"id {sid} not in {a.transcript}")
            by_id[sid]["zh"] = zh
            n += 1
    save(a.transcript, segs)
    print(f"applied {n} translations to {a.transcript}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge")
    m.add_argument("input")
    m.add_argument("-o", "--output", default="transcript.json")
    m.add_argument("--gap", type=float, default=1.0)
    m.add_argument("--max-sec", type=float, default=25.0)
    m.set_defaults(fn=cmd_merge)
    t = sub.add_parser("todo")
    t.add_argument("transcript")
    t.add_argument("--plan", required=True)
    t.add_argument("-o", "--output", default="todo.tsv")
    t.set_defaults(fn=cmd_todo)
    p = sub.add_parser("apply")
    p.add_argument("transcript")
    p.add_argument("tsv")
    p.set_defaults(fn=cmd_apply)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
