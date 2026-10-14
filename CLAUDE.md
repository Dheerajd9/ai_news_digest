# Rules for working on this repo

- Every change must also update the README it affects (`README.md` for the AI News Digest /
  Portfolio Report, `world_brief/README.md` for the World Brief) in the same commit, and bump
  the "Last updated" date.
- Before pushing any World Brief change, run `python world_brief/tests/e2e.py` (needs ffmpeg);
  it must print "ALL CHECKS PASSED". Add a check to it for every new feature.
- Never commit personal data: holdings live only in the `PORTFOLIO_JSON` secret; family
  addresses only in secrets. This repo is public.
- Everything must stay free (no paid APIs, no card-required services).
- Run-now requests: add a line `run: ...` to `world_brief/run_request.txt` (see world_brief/README.md).
