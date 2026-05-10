import sys
import json
import re
import subprocess
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 release.py <version>")
        print("Example: python3 release.py 0.1.9")
        sys.exit(1)

    version = sys.argv[1]
    
    # Paths
    manifest_path = Path("custom_components/reolink_ha_sekurity/manifest.json")
    js_path = Path("custom_components/reolink_ha_sekurity/frontend/reolink-ha-sekurity-card.js")
    
    print(f"Bumping version to {version}...")
    
    # 1. Update manifest.json
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    manifest["version"] = version
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
        
    # 2. Update frontend card JS
    with open(js_path, "r", encoding="utf-8") as f:
        js_content = f.read()
    
    # Replace const CARD_VERSION = "..."
    js_content = re.sub(r'const CARD_VERSION = ".*?";', f'const CARD_VERSION = "{version}";', js_content)
    
    with open(js_path, "w", encoding="utf-8") as f:
        f.write(js_content)
        
    print("Files updated successfully. Proceeding with Git commit and push...")
    
    # 3. Git commit and push
    try:
        subprocess.run(["git", "add", str(manifest_path), str(js_path)], check=True)
        subprocess.run(["git", "commit", "-m", f"chore: release v{version}"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to commit/push to main: {e}")
        sys.exit(1)

    # 4. Tag release
    print(f"Tagging release v{version}...")
    try:
        subprocess.run(["git", "tag", f"v{version}"], check=True)
        subprocess.run(["git", "push", "origin", f"v{version}"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Failed to tag/push release: {e}")
        sys.exit(1)

    print(f"✅ Release v{version} deployed successfully!")

if __name__ == "__main__":
    main()
