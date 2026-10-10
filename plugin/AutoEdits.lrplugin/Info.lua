--[[ Auto Edits: Lightroom Classic plug-in.
     Version 0.2: reference exports for tuning the Auto Edits preview engine. ]]
return {
    LrSdkVersion = 10.0,
    LrSdkMinimumVersion = 6.0,
    LrToolkitIdentifier = 'solutions.cele-brat.autoedits',
    LrPluginName = 'Auto Edits',
    LrPluginInfoUrl = 'https://edits.cele-brat.solutions',

    -- Library > Plug-in Extras
    LrLibraryMenuItems = {
        { title = 'Make reference exports (selected photos)', file = 'ReferenceExports.lua' },
    },
    -- File > Plug-in Extras
    LrExportMenuItems = {
        { title = 'Make reference exports (selected photos)', file = 'ReferenceExports.lua' },
    },

    VERSION = { major = 0, minor = 2, revision = 0 },
}
