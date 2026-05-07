import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ['VANTRA_ENDPOINT'] = 'http://localhost:3000/api/v1/ingest'

import vantra

vantra.init(api_key="van_live_c5ef7a67e398091b57e7af3b92c1bba7a89d75fbe131fb4b", project="my-agent")

@vantra.trace(name="prompt_version_test", prompt_version="v2")
def run():
    return "hello"

run()
time.sleep(1)
print("done — check localhost:3000/dashboard/traces for a trace called prompt_version_test")
