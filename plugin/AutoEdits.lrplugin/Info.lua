--[[ Auto Edits: Lightroom Classic plug-in.
     Version 0.4: sends photos to the Auto Edits server; reference and calibration exports for tuning. ]]
return {
    LrSdkVersion = 10.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'solutions.cele-brat.autoedits',
    LrPluginName = 'Auto Edits',
    LrPluginInfoUrl = 'https://edits.cele-brat.solutions',

    -- Library > Plug-in Extras
    LrLibraryMenuItems = {
        { title = 'Auto Edits: send photos to the server', file = 'SendToServer.lua' },
        { title = 'Auto Edits: server settings', file = 'Settings.lua' },
        { title = 'Make reference exports (selected photos)', file = 'ReferenceExports.lua' },
        { title = 'Make calibration exports (4 to 6 photos)', file = 'CalibrationExports.lua' },
    },
    -- File > Plug-in Extras
    LrExportMenuItems = {
        { title = 'Auto Edits: send photos to the server', file = 'SendToServer.lua' },
        { title = 'Make reference exports (selected photos)', file = 'ReferenceExports.lua' },
    },

    VERSION = { major = 0, minor = 4, revision = 0 },
}
