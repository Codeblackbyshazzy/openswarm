// @electron/notarize 3.x is ESM-only, so a top-level require() throws
// ERR_REQUIRE_ESM the moment electron-builder loads this afterSign hook — which
// it does for EVERY platform/build, breaking even unsigned Windows packaging.
// Import it lazily, after the skip checks, so it's only loaded when we actually
// notarize (signed macOS). Dynamic import() works from CommonJS.
exports.default = async function notarizing(context) {
  const { electronPlatformName, appOutDir } = context;
  if (electronPlatformName !== 'darwin') return;

  if (process.env.CSC_IDENTITY_AUTO_DISCOVERY === 'false') {
    console.log('Skipping notarization (CSC_IDENTITY_AUTO_DISCOVERY=false)');
    return;
  }

  if (!process.env.APPLE_ID || !process.env.APPLE_TEAM_ID) {
    console.log('Skipping notarization (APPLE_ID or APPLE_TEAM_ID not set)');
    return;
  }

  const { notarize } = await import('@electron/notarize');

  const appName = context.packager.appInfo.productFilename;
  const appPath = `${appOutDir}/${appName}.app`;

  console.log(`Notarizing ${appPath}...`);

  await notarize({
    appBundleId: 'com.clusterlabs.openswarm',
    appPath,
    appleId: process.env.APPLE_ID,
    appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD,
    teamId: process.env.APPLE_TEAM_ID,
  });

  console.log('Notarization complete.');
};
