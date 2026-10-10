--[[ Auto Edits: Lightroom Classic plug-in.
     Version 0.3: reference and calibration exports for tuning the Auto Edits preview engine. ]]
return {
    LrSdkVersion = 10.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'solutions.cele-brat.autoedits',
    LrPluginName = 'Auto Edits',
    LrPluginInfoUrl = 'https://edits.cele-brat.solutions',

    -- Library > Plug-in Extras
    LrLibraryMenuItems = {
        { title = 'Make reference exports (selected photos)', file = 'ReferenceExports.lua' },
        { title = 'Make calibration exports (4 to 6 photos)', file = 'CalibrationExports.lua' },
    },
    -- File > Plug-in Extras
    LrExportMenuItems = {
        { title = 'Make reference exports (selected photos)', file = 'ReferenceExports.lua' },
    },

    VERSION = { major = 0, minor = 3, revision = 0 },
}
