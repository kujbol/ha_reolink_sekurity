# Release Instructions

To release a new version of the **Reolink HA Sekurity** integration so that HACS users can seamlessly update it, use the automated Python release script. This script automatically:
1. Bumps the integration version in `manifest.json`.
2. Bumps the frontend card version (`CARD_VERSION`) in the custom JavaScript UI.
3. Commits the changes to the `main` branch.
4. Creates and pushes the required lightweight Git tag so HACS can detect the new release.

### How to use:

Run the script from the root of the project with the desired new version:

```bash
python3 release.py 0.1.9
```

Once the script finishes successfully, the tag is pushed and HACS will automatically detect the new release version and prompt users to update.
