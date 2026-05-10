import sys
import json
import re
import subprocess
from pathlib import Path

def main():
    # Paths
    manifest_path = Path("custom_components/reolink_ha_sekurity/manifest.json")
    js_path = Path("custom_components/reolink_ha_sekurity/frontend/reolink-ha-sekurity-card.js")
    
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    if len(sys.argv) < 2:
        current_version = manifest.get("version", "0.0.0")
        parts = current_version.split(".")
        if len(parts) >= 3 and parts[-1].isdigit():
            parts[-1] = str(int(parts[-1]) + 1)
            version = ".".join(parts)
            print(f"No version provided. Auto-bumping from {current_version} to {version}")
        else:
            print("Failed to auto-bump current version. Please provide manually: python3 release.py <version>")
            sys.exit(1)
    else:
        version = sys.argv[1]
    
    print(f"Bumping version to {version}...")
    
    # 1. Update manifest.json
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

    # 5. Create GitHub Release
    print(f"Creating GitHub Release v{version}...")
    import shutil
    if shutil.which("gh"):
        try:
            subprocess.run(["gh", "release", "create", f"v{version}", "--generate-notes"], check=True)
        except subprocess.CalledProcessError as e:
            print(f"⚠️ Failed to create GitHub release: {e}")
            print("The tag was pushed, but you may need to create the release manually on GitHub.")
    else:
        print("⚠️ GitHub CLI (gh) is not installed. The tag was pushed, but the release could not be created automatically.")
        print("Please install gh (e.g., brew install gh) or create the release manually on GitHub.")

    print(f"✅ Release v{version} deployed successfully!")

if __name__ == "__main__":
    main()
