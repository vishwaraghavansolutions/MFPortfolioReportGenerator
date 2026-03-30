# ── Step 1: Rebuild and upload the benchmark master CSV ───────────────────────
# Fetches every scheme from mfapi.in (one request per scheme — takes a few minutes)
# Writes data/mf_benchmark_map.csv locally and uploads to
# gs://winrich_shared/master/mf_benchmark_map.csv

# from agents.mf_benchmark_agent import MutualFundBenchmarkAgent
# from agents.base import AgentStatus

# agent = MutualFundBenchmarkAgent()

# print("Rebuilding benchmark master... (this will take a few minutes)")
# resp = agent.run("rebuild_benchmark_master", {
#     # Optional: limit to first N schemes for a quick test
#     # "max_schemes": 50,
# })

# # ── Step 2: Verify data loads from GCS ───────────────────────────────────────
# resp = agent.run("get_amc_list", {})
# if resp.status == AgentStatus.SUCCESS:
#     print(f"AMCs found: {resp.output['count']} across {resp.metadata['total_schemes']} schemes")
# else:
#     print(f"Load failed: {resp.error}")


import imaplib, email

mail = imaplib.IMAP4_SSL("outlook.office365.com")
mail.login("knsridharan@wawya.in", "KnsWawya@12#")
mail.select("INBOX")

status, messages = mail.search(None, "UNSEEN")
for msg_id in messages[0].split():
    status, msg_data = mail.fetch(msg_id, "(RFC822)")
    msg = email.message_from_bytes(msg_data[0][1])
