# Package releases

Observer publishes four distributions from one source tag:

| Registry | Distribution |
| --- | --- |
| PyPI | `magic-lab-observer` |
| PyPI | `magic-lab-observer-cli` |
| PyPI | `magic-lab-observer-backend` |
| npm | `@magic-lab-studio/observer` |

**npm is suspended.** The maintainers lost access to the npm account that owns
`@magic-lab-studio/observer` (recovery requested from npm support), and the 2026-10-26 `0.2.0`
publish failed with `E404` at the trusted-publishing step. The `all` target publishes the Python
distributions only; `npm` stays available as an explicit target for when access is restored. Until
then the TypeScript SDK is installed from source (`npm pack`, see the README).

Publishing uses GitHub Actions trusted publishing. Registry credentials must
not be stored as repository secrets.

## One-time registry configuration

Create protected GitHub environments named `release`, `release-cli`, and
`release-backend`. PyPI requires each pending project to use a distinct trusted
publisher identity, so configure the Python distributions as follows:

| PyPI project | Owner | Repository | Workflow | Environment |
| --- | --- | --- | --- | --- |
| `magic-lab-observer` | `Magic-Lab-Studio` | `observer` | `publish.yml` | `release` |
| `magic-lab-observer-cli` | `Magic-Lab-Studio` | `observer` | `publish.yml` | `release-cli` |
| `magic-lab-observer-backend` | `Magic-Lab-Studio` | `observer` | `publish.yml` | `release-backend` |

For a Python name that has not been published yet, create a pending publisher
under the PyPI organization. The first successful workflow run creates the
project. Existing projects use the same values under their publishing settings.

On npm, configure the trusted publisher for
`@magic-lab-studio/observer` with organization `Magic-Lab-Studio`, repository
`observer`, workflow `publish.yml`, and environment `release`. Keep token-based
publishing disabled after an OIDC release succeeds.

## Release procedure

1. Update all four package versions to the same semantic version.
2. Merge the version change to `main` after CI passes.
3. Create and push a signed or annotated `v<version>` tag on that commit.
4. Run the `Publish packages` workflow from `main`, enter that existing tag,
   and choose `all`, `python`, or `npm`.
5. Approve each protected package environment after reviewing the build jobs.
6. Verify each registry from a clean environment before creating or updating
   the GitHub release.

The workflow checks that the tag and package versions match, builds packages in
jobs without publishing permission, and grants OIDC only to the publish jobs.
It never accepts arbitrary credentials or silently moves an existing tag.

Observer `0.1.1` established all four registry projects. For later releases,
update every package to the same new version before selecting `all`; registries
do not permit republishing an existing version.
