# ACBI

**[Open the live demo](https://thereafter-revision-acids-climbing.trycloudflare.com)**

This is the full application, hosted on the owner's PC. Login is required;
request credentials from the owner. The temporary link works only while the PC,
Docker, and tunnel are running and changes when the tunnel restarts.
See [demo operation and verification](docs/LOCAL_DEMO.md).
See [current components, role accounts, and limitations](docs/CURRENT_STATUS.md).

Phase 4 of the AI-Powered Conversational Business Intelligence prototype. The local AdventureWorks PostgreSQL warehouse stays external to ACBI. Approved metrics, role-filtered questions, validated charts, saved results, editable voice transcripts, and local administration are available. The owner approved the documented Groq business context, and live advanced analysis is enabled locally.

Start with `./scripts/acbi.ps1 up` on Windows, then open http://localhost:8080. Run `./scripts/acbi.ps1 seed-users` once and open the private `deploy/seed-credentials.txt` file for local login. With Make available, `make up` starts the same local configuration. Exactly three ACBI services run: web, backend and db. The application database is internal-only.

- [Assumptions and approval decisions](docs/ASSUMPTIONS.md)
- [Phase 0 report](docs/PHASE_0_REPORT.md)
- [Phase 1 report](docs/PHASE_1_REPORT.md)
- [Phase 2 report](docs/PHASE_2_REPORT.md)
- [Phase 3 report](docs/PHASE_3_REPORT.md)
- [Phase 4 report](docs/PHASE_4_REPORT.md)
- [Proposed Groq context](docs/PHASE_3_EXTERNAL_CONTEXT.md)
- [Runbook](docs/RUNBOOK.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Oracle Cloud deployment](docs/CLOUD_DEPLOYMENT.md) — optional cloud profile; not deployed.
- [Approved dictionary](data/business_dictionary/dictionary.yaml)
- [Golden questions](data/eval/golden_questions.yaml)

Default model: Groq `openai/gpt-oss-120b`; voice transcription uses `whisper-large-v3-turbo`. The 40-case trusted query evaluation, six-case generated-query evaluation, and Phase 4 checks use FakeLLM or FakeSTT. One approved live Groq generated-query smoke test passed. Local evaluations do not establish broad live-model accuracy. The private `deploy/.env` enables metadata use but keeps result-row export disabled.
