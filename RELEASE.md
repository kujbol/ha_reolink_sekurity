# Release Instructions

To release a new version of the **Reolink HA Sekurity** integration so that HACS users can seamlessly update it:

1. **Bump the Version:**
   Update the `version` field in `custom_components/reolink_ha_sekurity/manifest.json` (e.g., to `"0.1.6"`).

2. **Commit and Push Changes:**
   Commit all changes along with the version bump to the `main` branch.
   ```bash
   git add custom_components/reolink_ha_sekurity/manifest.json
   git commit -m "chore: bump version to v0.1.6"
   git push origin main
   ```

3. **Tag the Release:**
   Create a lightweight git tag matching the version (prefixed with `v`) and push it to the remote repository. HACS strictly relies on these tags to identify new releases.
   ```bash
   git tag v0.1.6
   git push origin v0.1.6
   ```

Once the tag is pushed, HACS will automatically detect the new release version and prompt users to update.
