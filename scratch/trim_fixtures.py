import json
import os

def trim_json(path, key_to_trim, preserve_keys=None, is_list=False):
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if is_list:
        data = data[:3]
        if preserve_keys:
            for item in data:
                keys_to_delete = [k for k in item.keys() if k not in preserve_keys]
                for k in keys_to_delete:
                    del item[k]
    else:
        if key_to_trim in data:
            data[key_to_trim] = data[key_to_trim][:3]
            if preserve_keys:
                for item in data[key_to_trim]:
                    keys_to_delete = [k for k in item.keys() if k not in preserve_keys]
                    for k in keys_to_delete:
                        del item[k]

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def trim_html(path):
    if not os.path.exists(path):
        return
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    if len(content) > 200000:
        content = content[:200000]
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

# greenhouse_cobaltio_jobs.json -> keep all keys in jobs but only first 3
trim_json("tests/fixtures/ats/greenhouse_cobaltio_jobs.json", "jobs")

# lever_spotify_postings.json
preserve_lever = ["description", "descriptionPlain", "categories", "country", "workplaceType", "createdAt"]
trim_json("tests/fixtures/ats/lever_spotify_postings.json", None, preserve_keys=preserve_lever, is_list=True)

# ashby_ramp_jobs.json
preserve_ashby = ["descriptionHtml", "descriptionPlain", "isRemote", "workplaceType", "address", "secondaryLocations", "compensation", "publishedAt"]
trim_json("tests/fixtures/ats/ashby_ramp_jobs.json", "jobs", preserve_keys=preserve_ashby)

html_files = [
    "tests/fixtures/html/cobaltio_careers.html",
    "tests/fixtures/html/newrocket_careers.html",
    "tests/fixtures/html/ramp_careers.html",
    "tests/fixtures/html/lifeatspotify_careers.html"
]

for f in html_files:
    trim_html(f)

# write empty ones
with open("tests/fixtures/ats/greenhouse_empty.json", "w") as f:
    f.write('{"jobs": [], "meta": {"total": 0}}')
with open("tests/fixtures/ats/lever_empty.json", "w") as f:
    f.write('[]')
with open("tests/fixtures/ats/ashby_empty.json", "w") as f:
    f.write('{"jobs": []}')

# greenhouse_cobaltio_job_content.json -> take 1st job
if os.path.exists("tests/fixtures/ats/greenhouse_cobaltio_jobs.json"):
    with open("tests/fixtures/ats/greenhouse_cobaltio_jobs.json") as f:
        d = json.load(f)
        if d.get("jobs"):
            with open("tests/fixtures/ats/greenhouse_cobaltio_job_content.json", "w") as out:
                json.dump(d["jobs"][0], out, indent=2)

# generic_nonats_careers.html
nonats = """<!DOCTYPE html>
<html>
<head><title>Careers at Generic</title></head>
<body>
  <h1>Careers</h1>
  <div class="job-card">
    <h2>Software Engineer</h2>
    <p>San Francisco, CA</p>
    <a href="https://generic.com/careers/software-engineer">Apply</a>
  </div>
  <div class="job-card">
    <h2>Product Manager</h2>
    <p>Remote</p>
    <a href="https://generic.com/careers/product-manager">Apply</a>
  </div>
  <div class="job-card">
    <h2>Designer</h2>
    <p>New York, NY</p>
    <a href="https://generic.com/careers/designer">Apply</a>
  </div>
</body>
</html>
"""
with open("tests/fixtures/html/generic_nonats_careers.html", "w") as f:
    f.write(nonats)
