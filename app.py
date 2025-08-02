import os
import requests
import tempfile
import zipfile
from flask import Flask, request, jsonify, send_file, abort, render_template_string

app = Flask(__name__)

MODRINTH_API_URL = "https://api.modrinth.com/v2"
MODS_CACHE_DIR = "downloaded_mods"

if not os.path.exists(MODS_CACHE_DIR):
    os.makedirs(MODS_CACHE_DIR)

def fetch_fabric_mods(limit=50):
    """Fetch trending Fabric mods from Modrinth"""
    url = f"{MODRINTH_API_URL}/search"
    params = {
        "facets": '[["categories:fabric"]]',
        "limit": limit,
        "index": "relevance"
    }
    resp = requests.get(url, params=params)
    if resp.status_code == 200:
        return [{"slug": mod["slug"], "name": mod["title"]} for mod in resp.json()["hits"]]
    return []

def fetch_mod_versions(slug, mc_version):
    """Fetch mod versions compatible with Minecraft version and Fabric loader"""
    url = f"{MODRINTH_API_URL}/project/{slug}/version"
    params = {
        "loaders": '["fabric"]',
        "game_versions": f'["{mc_version}"]'
    }
    resp = requests.get(url, params=params)
    if resp.status_code != 200:
        return []
    return resp.json()

def download_file(url, save_path):
    resp = requests.get(url, stream=True)
    if resp.status_code == 200:
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(1024 * 10):
                f.write(chunk)
        return True
    return False

def resolve_dependencies(version_data, mc_version):
    """
    For a given mod version, find required dependencies (recursive),
    fetch their latest compatible versions and collect all files.
    """
    files_to_download = {}

    def add_version_files(ver):
        for f in ver["files"]:
            files_to_download[f["filename"]] = f["url"]

    to_process = [version_data]
    processed_projects = set()

    while to_process:
        ver = to_process.pop()
        project_id = ver["project_id"]
        if project_id in processed_projects:
            continue
        processed_projects.add(project_id)

        add_version_files(ver)

        # Check dependencies
        for dep in ver.get("dependencies", []):
            if dep["dependency_type"] == "required":
                dep_proj_id = dep["project_id"]
                # Fetch project info
                proj_resp = requests.get(f"{MODRINTH_API_URL}/project/{dep_proj_id}")
                if proj_resp.status_code != 200:
                    continue
                slug = proj_resp.json()["slug"]

                dep_versions = fetch_mod_versions(slug, mc_version)
                if not dep_versions:
                    continue
                to_process.append(dep_versions[0])  # take latest compatible version

    return files_to_download

@app.route('/')
def index():
    return render_template_string('''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>Fabric Mod Downloader</title>
<style>
  body { font-family: Arial, sans-serif; max-width: 700px; margin: auto; padding: 20px;}
  input[type=text] { width: 200px; padding: 5px; }
  #mods-list { max-height: 400px; overflow-y: scroll; border: 1px solid #ddd; margin-top: 10px; padding: 10px; }
  label { display: block; margin-bottom: 5px; }
  button { padding: 10px 20px; margin-top: 15px; }
  #progress { margin-top: 15px; }
</style>
</head>
<body>
<h2>Fabric Mod Downloader</h2>

<label>Minecraft Version: <input type="text" id="mcVersion" placeholder="e.g. 1.19.4" /></label>

<label>Search Mods: <input type="text" id="searchBox" placeholder="Search mods..." /></label>

<div id="mods-list">Loading mods...</div>

<button id="downloadBtn" disabled>Download Selected Mods</button>

<div id="progress"></div>

<script>
let mods = [];
let filteredMods = [];
const modsList = document.getElementById('mods-list');
const searchBox = document.getElementById('searchBox');
const downloadBtn = document.getElementById('downloadBtn');
const progressDiv = document.getElementById('progress');
const mcVersionInput = document.getElementById('mcVersion');

function renderMods(list) {
  modsList.innerHTML = '';
  if (list.length === 0) {
    modsList.innerHTML = '<em>No mods found</em>';
    downloadBtn.disabled = true;
    return;
  }
  list.forEach(mod => {
    const label = document.createElement('label');
    label.innerHTML = `<input type="checkbox" value="${mod.slug}"> ${mod.name}`;
    modsList.appendChild(label);
  });
  downloadBtn.disabled = false;
}

function filterMods() {
  const q = searchBox.value.toLowerCase();
  filteredMods = mods.filter(m => m.name.toLowerCase().includes(q));
  renderMods(filteredMods);
}

async function fetchMods() {
  modsList.innerHTML = 'Loading mods...';
  const resp = await fetch('/api/mods');
  if (!resp.ok) {
    modsList.innerHTML = '<em>Failed to load mods</em>';
    return;
  }
  mods = await resp.json();
  filteredMods = mods;
  renderMods(mods);
}

downloadBtn.onclick = async () => {
  const checked = [...modsList.querySelectorAll('input[type=checkbox]:checked')].map(cb => cb.value);
  const mcVersion = mcVersionInput.value.trim();
  if (!mcVersion) {
    alert("Please enter a Minecraft version.");
    return;
  }
  if (checked.length === 0) {
    alert("Please select at least one mod.");
    return;
  }
  downloadBtn.disabled = true;
  progressDiv.textContent = "Preparing download...";
  try {
    const response = await fetch('/api/download', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({mc_version: mcVersion, mods: checked})
    });
    if (!response.ok) {
      const err = await response.text();
      throw new Error(err || 'Failed to download mods');
    }
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'fabric_mods.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
    progressDiv.textContent = "Download completed!";
  } catch(e) {
    alert("Error: " + e.message);
    progressDiv.textContent = "";
  } finally {
    downloadBtn.disabled = false;
  }
};

searchBox.addEventListener('input', filterMods);

window.onload = fetchMods;
</script>

</body>
</html>
''')

@app.route('/api/mods')
def api_mods():
    mods = fetch_fabric_mods(50)
    return jsonify(mods)

@app.route('/api/download', methods=['POST'])
def api_download():
    data = request.json
    mc_version = data.get('mc_version')
    mod_slugs = data.get('mods', [])

    if not mc_version or not mod_slugs:
        abort(400, "Minecraft version and mod list required")

    # Collect files URLs from mods + dependencies
    files_to_download = {}

    for slug in mod_slugs:
        versions = fetch_mod_versions(slug, mc_version)
        if not versions:
            continue
        latest_ver = versions[0]
        deps_files = resolve_dependencies(latest_ver, mc_version)
        files_to_download.update(deps_files)

    if not files_to_download:
        abort(404, "No compatible mods found for the specified Minecraft version")

    # Create temporary zip
    tmp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    with zipfile.ZipFile(tmp_zip.name, 'w') as zipf:
        for filename, url in files_to_download.items():
            print(f"Downloading {filename} ...")
            try:
                r = requests.get(url, stream=True, timeout=15)
                if r.status_code == 200:
                    zipf.writestr(filename, r.content)
                else:
                    print(f"Failed to download {filename}")
            except Exception as e:
                print(f"Error downloading {filename}: {e}")

    # Send the zip back
    return send_file(tmp_zip.name, as_attachment=True, download_name='fabric_mods.zip')

if __name__ == '__main__':
    app.run(debug=True)
