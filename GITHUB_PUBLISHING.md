# GitHub publishing checklist

Suggested repository settings:

- Repository name: `ham-spotter`
- Description: `Real-time amateur-radio propagation dashboard for HF/VHF/UHF using PSK Reporter, RBN, DX Cluster and space-weather data.`
- Visibility: Public (after secret audit)
- Default branch: `main`
- Topics: `amateur-radio`, `ham-radio`, `propagation`, `pskreporter`, `reverse-beacon-network`, `dx-cluster`, `raspberry-pi`, `docker`, `fastapi`, `vhf`, `hf`

Before publishing:

1. Confirm the root `LICENSE` is present and identifies GNU GPL v3.0.
2. Search the complete tree for real credentials, personal `.env` values, database files and backups.
3. Confirm `.env`, `data/` and `backups/` are not staged.
4. Commit the source tree.
5. Publish the repository.
6. Create GitHub Release tag `v1.13.1`.
7. Upload the three release assets from the prepared release-assets bundle.
8. Paste `RELEASE_NOTES_1.13.1.md` into the release description.
