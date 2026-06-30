#ifndef KYVP_LIB_EXTENSION_H_
#define KYVP_LIB_EXTENSION_H_

#include "KYVPDefines.h"
#include "KYVPLibExtensionDefines.h"
#include "KYVPLibTL.h"
#include "KYVPParametersHandler.h"
#include "experimental/KYVPLogging.h"

#pragma pack(push, 1)
typedef struct _kyvp_update_status
{
    int      struct_version; // Currently code initializes this with "1". 
    // If more fields will be added to this stuct in future code will be changed and initialize it with "2", etc.
    // See functions 'alt_epcs_flash_validate' and 'alt_epcs_flash_write' where this field is initialized

// Fileds of initial 1-st version of this struct:
    uint64_t    bytes_sent; // bytes already sent
    uint64_t    total_size; // firmware file size
    KY_BOOL     is_writing; // true - writing new firmware
    // false    - validating new firmware

// Fields added in 2nd version of this struct:

}KYVP_UPDATE_STATUS;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////
/// CALLBACK FUNCTIONS
//////////////////////////////////////////////////////////////////////

/**
 * @page KYVPExtension KYVPExtension
 * \section KYVP_UPDATE_CALLBACK KYVP_UPDATE_CALLBACK
 *     // Firmware update status callback
 * \code{.c}
    typedef KY_BOOL(KYVP_CALLCONV *KYVP_UPDATE_CALLBACK)(const KYVP_UPDATE_STATUS* UpdateStatus, void* context);
 * \endcode
*/
typedef KY_BOOL(KYVP_CALLCONV *KYVP_UPDATE_CALLBACK)(const KYVP_UPDATE_STATUS* UpdateStatus, void* context);

/**
 * @page KYVPExtension KYVPExtension
 * \section KYVP_DATA_STREAM_CALLBACK KYVP_DATA_STREAM_CALLBACK
 *     // Data stream callback function
 * \code{.c}
    typedef void(KYVP_CALLCONV * KYVP_DATA_STREAM_CALLBACK)(KYVP_BUFFER_HANDLE _phBufferHandle, void* _pContext);
 * \endcode
*/
typedef void(KYVP_CALLCONV * KYVP_DATA_STREAM_CALLBACK)(KYVP_BUFFER_HANDLE _phBufferHandle, void* _pContext);

/**
 * @page KYVPExtension KYVPExtension
 * \section KYVP_AUX_DATA_CALLBACK KYVP_AUX_DATA_CALLBACK
 *     The callback function will be called when various auxiliary data is generated. Auxiliary data is passed to user
    callback as a pointer to the KYVP_AUX_DATA structure parameter. Each Auxiliary data will be interpreted
    differently according to context and message ID.
 * \code{.c}
    typedef void(KYVP_CALLCONV *KYVP_AUX_DATA_CALLBACK)(KYVP_AUX_DATA* pData, void* context);
 * \endcode
*/
typedef void (KYVP_CALLCONV* KYVP_AUX_DATA_CALLBACK)(const KYVP_AUX_DATA* _pData, void* _pContext);

/**
 * @page KYVPExtension KYVPExtension
 * \section KYVP_SERIAL_CALLBACK KYVP_SERIAL_CALLBACK
 *     Serial port (see clserkyi.h) callback
 * \code{.c}
    typedef void(KYVP_CALLCONV *KYVP_SERIAL_CALLBACK)(uint32_t _uChannel, uint16_t _uEventId, const void* _pData, uint32_t _uSize, void* _pContext);
 * \endcode
*/
typedef void(KYVP_CALLCONV* KYVP_SERIAL_CALLBACK)(uint32_t _uChannel, uint16_t _uEventId, const void* _pData, uint32_t _uSize, void* _pContext);

/**
 * @page KYVPExtension KYVPExtension
 * \section KYVP_PCI_INTERFACE_EVENT_CALLBACK KYVP_PCI_INTERFACE_EVENT_CALLBACK
 *     PCI Interface events callback
 * \code{.c}
    typedef void(KY_CALLCONV* KYVP_PCI_INTERFACE_EVENT_CALLBACK)(KYVP_PCIINTERFACE_GENERIC_EVENT* _pGenericEventData, void* _pUserContext);
 * \endcode
*/
typedef void(KY_CALLCONV* KYVP_PCI_INTERFACE_EVENT_CALLBACK)(KYVP_PCIINTERFACE_GENERIC_EVENT* _pGenericEventData, void* _pUserContext);

//////////////////////////////////////////////////////////////////////
/// ARGUMENT STRUCTURES
//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACE_FIRMWAREUPDATE_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACE_FIRMWAREUPDATE_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_FirmwareUpdate_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACE_FIRMWAREUPDATE_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    char*                                   pszFilePath KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_UPDATE_CALLBACK                    pCallbackFunction KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pUserContext KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_PCIInterface_FirmwareUpdate_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACE_CHECKUPDATEFILE_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACE_CHECKUPDATEFILE_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_CheckUpdateFile_Args
{
    uint32_t                                version             KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACE_CHECKUPDATEFILE_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle           KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);

    uint16_t                                fileMinorRev    KYVP_MEMBER_DEFAULT(0);
    uint16_t                                fileMajorRev    KYVP_MEMBER_DEFAULT(0);
    uint16_t                                fileVendorId    KYVP_MEMBER_DEFAULT(0);
    uint16_t                                fileBoardId     KYVP_MEMBER_DEFAULT(0);
    uint32_t                                fileVersion     KYVP_MEMBER_DEFAULT(0);
    uint32_t                                fileTimeStamp   KYVP_MEMBER_DEFAULT(0);
    uint16_t                                flashMinorRev   KYVP_MEMBER_DEFAULT(0);
    uint16_t                                flashMajorRev   KYVP_MEMBER_DEFAULT(0);
    uint32_t                                flashVendorId   KYVP_MEMBER_DEFAULT(0);
    uint32_t                                flashBoardId    KYVP_MEMBER_DEFAULT(0);
    uint32_t                                flashVersion    KYVP_MEMBER_DEFAULT(0);
    uint32_t                                flashTimeStamp  KYVP_MEMBER_DEFAULT(0);
    char*                                   pszFilePath     KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_PCIInterface_CheckUpdateFile_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACE_EVENTCALLBACKREGISTER_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACE_EVENTCALLBACKREGISTER_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_EventCallBackRegister_Args
{
    uint32_t                                    version      KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACE_EVENTCALLBACKREGISTER_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE                   hIFHandle    KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    KYVP_PCI_INTERFACE_EVENT_CALLBACK           pUserFunc    KYVP_MEMBER_DEFAULT(nullptr);
    void*                                       pUserContext KYVP_MEMBER_DEFAULT(nullptr);
}KYVPExtension_PCIInterface_EventCallBackRegister_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACE_EVENTCALLBACKUNREGISTER_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACE_EVENTCALLBACKUNREGISTER_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_EventCallBackUnregister_Args
{
    uint32_t                                    version     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACE_EVENTCALLBACKUNREGISTER_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE                   hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    KYVP_PCI_INTERFACE_EVENT_CALLBACK           pUserFunc    KYVP_MEMBER_DEFAULT(nullptr);
}KYVPExtension_PCIInterface_EventCallBackUnregister_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_REMOTEDEVICEDIRECTHWREADREG_STRUCT_VERSION = 1;
#else
#define KYVPEXTENSION_REMOTEDEVICEDIRECTHWREADREG_STRUCT_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_RemoteDevice_DirectHWReadReg_Args KYVPExtension_RemoteDevice_DirectHWReadReg_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hRemoteDeviceHandle| @ref KYVP_REMOTE_DEVICE_HANDLE    | RemoteDevice Handle                                   |           |
 * | iAddress           | uint64_t                          | Register address                                      |           |
 * | pBuffer            | void*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_RemoteDevice_DirectHWReadReg_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPEXTENSION_REMOTEDEVICEDIRECTHWREADREG_STRUCT_VERSION);
    KYVP_REMOTE_DEVICE_HANDLE               hRemoteDeviceHandle     KYVP_MEMBER_DEFAULT(KYVP_REMOTE_DEVICE_HANDLE_INVALID);
    uint64_t                                iAddress                KYVP_MEMBER_DEFAULT(0);
    void*                                   pBuffer                 KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize                  KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_RemoteDevice_DirectHWReadReg_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_REMOTEDEVICEDIRECTHWWRITEREG_STRUCT_VERSION = 1;
#else
#define KYVPEXTENSION_REMOTEDEVICEDIRECTHWWRITEREG_STRUCT_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_RemoteDevice_DirectHWWriteReg_Args KYVPExtension_RemoteDevice_DirectHWWriteReg_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hRemoteDeviceHandle| @ref KYVP_REMOTE_DEVICE_HANDLE    | RemoteDevice Handle                                   |           |
 * | iAddress           | uint64_t                          | Register address                                      |           |
 * | pBuffer            | void*                             | Pointer to user allocated const buffer                |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_RemoteDevice_DirectHWWriteReg_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPEXTENSION_REMOTEDEVICEDIRECTHWWRITEREG_STRUCT_VERSION);
    KYVP_REMOTE_DEVICE_HANDLE               hRemoteDeviceHandle     KYVP_MEMBER_DEFAULT(KYVP_REMOTE_DEVICE_HANDLE_INVALID);
    uint64_t                                iAddress                KYVP_MEMBER_DEFAULT(0);
    const void*                             pBuffer                 KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize                  KYVP_MEMBER_DEFAULT(nullptr);

} KYVPExtension_RemoteDevice_DirectHWWriteReg_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_REMOTEDEVICE_SETIMAGEDETAILS_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_REMOTEDEVICE_SETIMAGEDETAILS_ARGS_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_RemoteDevice_SetImageDetails_Args KYVPExtension_RemoteDevice_SetImageDetails_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hRemoteDeviceHandle| @ref KYVP_REMOTE_DEVICE_HANDLE    | RemoteDevice Handle                                   |           |
 * | imageDetails       | KYVP_DEVICE_IMAGE_DETAILS         | Image details struct                                  |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_RemoteDevice_SetImageDetails_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPEXTENSION_REMOTEDEVICE_SETIMAGEDETAILS_ARGS_VERSION);
    KYVP_REMOTE_DEVICE_HANDLE               hRemoteDeviceHandle     KYVP_MEMBER_DEFAULT(KYVP_REMOTE_DEVICE_HANDLE_INVALID);
    KYVP_DEVICE_IMAGE_DETAILS               imageDetails;

} KYVPExtension_RemoteDevice_SetImageDetails_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACEDIRECTHWREADREG_STRUCT_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACEDIRECTHWREADREG_STRUCT_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_PCIInterface_DirectHWReadReg_Args KYVPExtension_PCIInterface_DirectHWReadReg_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hIFHandle          | @ref KYVP_PCI_INTERFACE_HANDLE    | PCI Interface Handle                                  |           |
 * | iBar               | uint64_t                          | Address space (reserved for internal usage, must be 0)|           |
 * | iAddress           | uint64_t                          | Register address                                      |           |
 * | pBuffer            | void*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_DirectHWReadReg_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACEDIRECTHWREADREG_STRUCT_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                                iBar        KYVP_MEMBER_DEFAULT(0);
    uint64_t                                iAddress    KYVP_MEMBER_DEFAULT(0);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_PCIInterface_DirectHWReadReg_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACEDIRECTHWWRITEREG_STRUCT_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACEDIRECTHWWRITEREG_STRUCT_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_PCIInterface_DirectHWWriteReg_Args KYVPExtension_PCIInterface_DirectHWWriteReg_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hIFHandle          | @ref KYVP_PCI_INTERFACE_HANDLE    | Interface Handle                                      |           |
 * | iBar               | uint64_t                          | Address space (reserved for internal usage, must be 0)|           |
 * | iAddress           | uint64_t                          | Register address                                      |           |
 * | pBuffer            | void*                             | Pointer to user allocated const buffer                |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_DirectHWWriteReg_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACEDIRECTHWWRITEREG_STRUCT_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                                iBar        KYVP_MEMBER_DEFAULT(0);
    uint64_t                                iAddress    KYVP_MEMBER_DEFAULT(0);
    const void*                             pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPExtension_PCIInterface_DirectHWWriteReg_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACELOCKLINK_STRUCT_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACELOCKLINK_STRUCT_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_PCIInterface_LockLink_Args KYVPExtension_PCIInterface_LockLink_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hIFHandle          | @ref KYVP_PCI_INTERFACE_HANDLE    | PCI Interface Handle                                  |           |
 * | iLink              | uint64_t                          | PCI Interface link index                              |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_LockLink_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACELOCKLINK_STRUCT_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                                iLink       KYVP_MEMBER_DEFAULT(0);

}KYVPExtension_PCIInterface_LockLink_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACEUNLOCKLINK_STRUCT_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACEUNLOCKLINK_STRUCT_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_PCIInterface_UnlockLink_Args KYVPExtension_PCIInterface_UnlockLink_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hIFHandle          | @ref KYVP_PCI_INTERFACE_HANDLE    | PCI Interface Handle                                  |           |
 * | iLink              | uint64_t                          | PCI Interface link index                              |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_UnlockLink_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACEUNLOCKLINK_STRUCT_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                                iLink       KYVP_MEMBER_DEFAULT(0);

}KYVPExtension_PCIInterface_UnlockLink_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACEREADLINK_STRUCT_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACEREADLINK_STRUCT_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_PCIInterface_ReadLink_Args KYVPExtension_PCIInterface_ReadLink_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hIFHandle          | @ref KYVP_PCI_INTERFACE_HANDLE    | PCI Interface Handle                                  |           |
 * | iLink              | uint64_t                          | PCI Interface link index                              |           |
 * | iAddress           | uint64_t                          | Register address                                      |           |
 * | pBuffer            | void*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_ReadLink_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACEREADLINK_STRUCT_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                                iLink       KYVP_MEMBER_DEFAULT(0);
    uint64_t                                iAddress    KYVP_MEMBER_DEFAULT(0);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_PCIInterface_ReadLink_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACEWRITELINK_STRUCT_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACEWRITELINK_STRUCT_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_PCIInterface_WriteLink_Args KYVPExtension_PCIInterface_WriteLink_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hIFHandle          | @ref KYVP_PCI_INTERFACE_HANDLE    | PCI Interfacee Handle                                 |           |
 * | iLink              | uint64_t                          | PCI Interface link index                              |           |
 * | iAddress           | uint64_t                          | Register address                                      |           |
 * | pBuffer            | void*                             | Pointer to user allocated const buffer                |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_WriteLink_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACEWRITELINK_STRUCT_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                                iLink       KYVP_MEMBER_DEFAULT(0);
    uint64_t                                iAddress    KYVP_MEMBER_DEFAULT(0);
    const void*                             pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPExtension_PCIInterface_WriteLink_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACESENDLINKEVENT_STRUCT_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACESENDLINKEVENT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_PCIInterface_WriteLink_Args KYVPExtension_PCIInterface_WriteLink_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hIFHandle          | @ref KYVP_PCI_INTERFACE_HANDLE    | PCI Interfacee Handle                                 |           |
 * | iLink              | uint64_t                          | PCI Interface link index                              |           |
 * | iEventId           | uint16_t                          | Event ID                                              |           |
 * | pBuffer            | void*                             | Pointer to user allocated const buffer                |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_SendLinkEvent_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACEWRITELINK_STRUCT_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                                iLink       KYVP_MEMBER_DEFAULT(0);
    uint16_t                                iEventId    KYVP_MEMBER_DEFAULT(0);
    const void*                             pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPExtension_PCIInterface_SendLinkEvent_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_PCIINTERFACELINKGETSTATUS_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_PCIINTERFACELINKGETSTATUS_ARGS_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_PCIInterface_LinkGetStatus_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hIFHandle              | @ref KYVP_PCI_INTERFACE_HANDLE    | PCI Interface Handle                                      |           |
 * | iLink                  | uint64_t                          | PCI Interface link index                                  |           |
 * | iInfoCmd               | @ref KYVP_LINK_STATUS_CMD         | Information to be retrieved                               |           |
 * | piType                 | @ref KYVP_INFO_DATATYPE*          | Data type of the pBuffer content                          |           |
 * | pBuffer                | void*                             | Pointer to a user allocated buffer                        |           |
 * | piSize                 | size_t*                           | Number of bytes filled by the function                    |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_PCIInterface_LinkGetStatus_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPEXTENSION_PCIINTERFACELINKGETSTATUS_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle       KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                                iLink           KYVP_MEMBER_DEFAULT(0);
    KYVP_LINK_STATUS_CMD                    iInfoCmd        KYVP_MEMBER_DEFAULT(KYVP_LINK_STATUS_CMD_LINK_CONNECTION_STATUS);
    KYVP_INFO_DATATYPE*                     piType          KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pBuffer         KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize          KYVP_MEMBER_DEFAULT(nullptr);

} KYVPExtension_PCIInterface_LinkGetStatus_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_AUX_DATA_CALLBACK_REGISTER_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_AUX_DATA_CALLBACK_REGISTER_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_AuxDataCallback_Register_Args
{
    uint32_t                                version            KYVP_MEMBER_DEFAULT(KYVPEXTENSION_AUX_DATA_CALLBACK_REGISTER_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle          KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    KYVP_AUX_DATA_CALLBACK                  pCallbackFunction  KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pUserContext       KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_AuxDataCallback_Register_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_AUX_DATA_CALLBACK_UNREGISTER_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_AUX_DATA_CALLBACK_UNREGISTER_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_AuxDataCallback_Unregister_Args
{
    uint32_t                                version            KYVP_MEMBER_DEFAULT(KYVPEXTENSION_AUX_DATA_CALLBACK_UNREGISTER_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle          KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    KYVP_AUX_DATA_CALLBACK                  pCallbackFunction  KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_AuxDataCallback_Unregister_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_DATASTREAMCREATE_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_DATASTREAMCREATE_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_DataStream_Create_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPEXTENSION_DATASTREAMCREATE_ARGS_VERSION);
    const char*                             pszDevDataStreamID      KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_STREAM_BUFFERS_TYPE                eStreamBufferType       KYVP_MEMBER_DEFAULT(KYVP_STREAM_BUFFERS_TYPE_QUEUED_BUFFERS);
    KYVP_STREAM_HANDLE*                     phDSHandle              KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_DataStream_Create_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_DATASTREAM_CLOSE_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_DATASTREAM_CLOSE_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_DataStream_Close_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPEXTENSION_DATASTREAM_CLOSE_ARGS_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle               KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);

}KYVPExtension_DataStream_Close_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_DATASTREAMCALLBACKREGISTER_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_DATASTREAMCALLBACK_REGISTER_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_DataStream_Callback_Register_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPEXTENSION_DATASTREAMCALLBACKREGISTER_ARGS_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle               KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    KYVP_DATA_STREAM_CALLBACK               pCallbackFunction       KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pContext                KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_DataStream_Callback_Register_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_DATASTREAMCALLBACKUNREGISTER_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_DATASTREAMCALLBACKUNREGISTER_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_DataStream_Callback_Unregister_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPEXTENSION_DATASTREAMCALLBACKUNREGISTER_ARGS_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle               KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    KYVP_DATA_STREAM_CALLBACK               pCallbackFunction       KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_DataStream_Callback_Unregister_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_SERIALCALLBACKREGISTER_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_SERIALCALLBACK_REGISTER_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_Serial_Callback_Register_Args
{
    uint32_t                   version                 KYVP_MEMBER_DEFAULT(KYVPEXTENSION_SERIALCALLBACKREGISTER_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE  hIFHandle               KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                   iLink                   KYVP_MEMBER_DEFAULT(0);
    KYVP_SERIAL_CALLBACK       pCallbackFunction       KYVP_MEMBER_DEFAULT(nullptr);
    void* pContext             KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_Serial_Callback_Register_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_SERIALCALLBACKUNREGISTER_ARGS_VERSION = 1;
#else
#define KYVPEXTENSION_SERIALCALLBACKUNREGISTER_ARGS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVPExtension_Serial_Callback_Unregister_Args
{
    uint32_t                   version                 KYVP_MEMBER_DEFAULT(KYVPEXTENSION_SERIALCALLBACKUNREGISTER_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE  hIFHandle               KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                   iLink                   KYVP_MEMBER_DEFAULT(0);
    KYVP_SERIAL_CALLBACK       pCallbackFunction       KYVP_MEMBER_DEFAULT(nullptr);

}KYVPExtension_Serial_Callback_Unregister_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_REMOTEDEVICE_GETPARAMSCOLLECTIONHANDLE_ARGS = 1;
#else
#define KYVPEXTENSION_REMOTEDEVICE_GETPARAMSCOLLECTIONHANDLE_ARGS 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section KYVPExtension_RemoteDevice_GetParamsCollectionHandle_Args KYVPExtension_RemoteDevice_GetParamsCollectionHandle_Args
 * | Structure Field                | Type                              | Description                                           | Remarks   |
 * | -------------                  | -----                             | -----------                                           | ----      |
 * | version                        | uint32_t                          | struct version                                        |           |
 * | hRemoteDeviceHandle            | @ref KYVP_REMOTE_DEVICE_HANDLE    | RemoteDevice Handle                                   |           |
 * | phParamsCollectionHandle       | KYVP_COLLECTION_HANDLE*           | Pointer to parameters collection handle               |           |
 */
#pragma pack(push, 1)
typedef struct _KYVPExtension_RemoteDevice_GetParamsCollectionHandle_Args
{
    uint32_t                                version                     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_REMOTEDEVICE_GETPARAMSCOLLECTIONHANDLE_ARGS);
    KYVP_REMOTE_DEVICE_HANDLE               hRemoteDeviceHandle         KYVP_MEMBER_DEFAULT(KYVP_REMOTE_DEVICE_HANDLE_INVALID);
    KYVP_COLLECTION_HANDLE*                 phParamsCollectionHandle    KYVP_MEMBER_DEFAULT(nullptr);
    

} KYVPExtension_RemoteDevice_GetParamsCollectionHandle_Args;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_DEVICELINKDEFINITION_VERSION = 1;
#else
#define KYVPEXTENSION_DEVICELINKDEFINITION_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section DeviceLinkDefinition DeviceLinkDefinition
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | Struct version                                        |           |
 * | uHostLink          | uint32_t                          | Host link index connected to the device link          |           |
 */
#pragma pack(push, 1)
typedef struct _DeviceLinkDefinition
{
    uint32_t        uVersion        KYVP_MEMBER_DEFAULT(KYVPEXTENSION_DEVICELINKDEFINITION_VERSION);

    // since version 1:
    uint32_t        uHostLink    KYVP_MEMBER_DEFAULT(uint32_t(-1));
} DeviceLinkDefinition;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYVPEXTENSION_DEVICEDEFINITION_VERSION = 1;
#else
#define KYVPEXTENSION_DEVICEDEFINITION_VERSION 1
#endif
/**
 * @page KYVPExtension_Structs KYVPExtension Structs
 * @section DeviceDefinition DeviceDefinition
 * | Structure Field                        | Type                              | Description                                                             | Remarks   |
 * | -------------                          | -----                             | -----------                                                             | ----      |
 * | version                                | uint32_t                          | Struct version                                                          |           |
 * | eConnectionSpeed                       | KYVP_CXP_LINK_SPEED               | Expected current connection speed of the device                         |           |
 * | pDeviceLinkDefinitionArray             | DeviceLinkDefinition*             | Pointer to an allocated array of DeviceLinkDefinition structures        |           |
 * | uDeviceLinkDefinitionArrayCount        | size_t                            | Number of elements in the allocated DeviceLinkDefinition array          |           |
 * | bFound                                 | KY_BOOL                           | Detection result flag - set to KY_TRUE if the device was found          |           |
 * | szID                                   | char[]                            | Transport layer device ID, filled if the device was found               |           |
 */
#pragma pack(push, 1)
typedef struct _DeviceDefinition
{
    uint32_t            uVersion                                                                KYVP_MEMBER_DEFAULT(KYVPEXTENSION_DEVICEDEFINITION_VERSION);

    // since version 1:
    KYVP_CXP_LINK_SPEED             eConnectionSpeed                                            KYVP_MEMBER_DEFAULT(KYVP_CXP_LINK_SPEED_CXP0);
    DeviceLinkDefinition*           pDeviceLinkDefinitionArray                                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t                          uDeviceLinkDefinitionArrayCount                             KYVP_MEMBER_DEFAULT(0);
    KY_BOOL                         bFound                                                      KYVP_MEMBER_DEFAULT(KY_FALSE);
    char                            szID[KY_MAX_DEVICE_INFO_STRING_SIZE + 1]                    KYVP_MEMBER_DEFAULT({0});
} DeviceDefinition;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPEXTENSION_DETECTDEVICESMANUALLY_ARGS = 1;
#else
#define KYVPEXTENSION_DETECTDEVICESMANUALLY_ARGS 1
#endif
/**
@page KYVPExtension_Structs KYVPExtension Structs
@section KYVPExtension_DetectDevicesManually_Args KYVPExtension_DetectDevicesManually_Args
| Structure Field               | Type                              | Description                                                                                       | Remarks   |
| ------------------------------| ----------------------------------| --------------------------------------------------------------------------------------------------|           |
| version                       | uint32_t                          | Struct version                                                                                    |           |
| hIFHandle                     | KYVP_PCI_INTERFACE_HANDLE         | Handle to the PCI interface instance                                                              |           |
| pbChanged                     | KY_BOOL*                          | Pointer to a boolean flag that indicates if the device list has changed                           |           |
| pDeviceDefinitionArray        | DeviceDefinition*                 | Pointer to an array of DeviceDefinition structures describing the devices                         |           |
| uDeviceDefinitionArrayCount   | size_t                            | Number of elements in pDeviceDefinitionArray                                                      |           |
| bNoDevicesAccess              | KY_BOOL                           | If true, no operations requiring access to remote device will be performed                        |           |
*/
#pragma pack(push, 1)
typedef struct _KYVPExtension_DetectDevicesManually_Args
{
    uint32_t                                version                                     KYVP_MEMBER_DEFAULT(KYVPEXTENSION_DETECTDEVICESMANUALLY_ARGS);

    // since version 1:
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle                                   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    KY_BOOL*                                pbChanged                                   KYVP_MEMBER_DEFAULT(nullptr);
    DeviceDefinition*                       pDeviceDefinitionArray                      KYVP_MEMBER_DEFAULT(nullptr);
    size_t                                  uDeviceDefinitionArrayCount                 KYVP_MEMBER_DEFAULT(0);
    KY_BOOL                                 bNoDevicesAccess                            KYVP_MEMBER_DEFAULT(KY_FALSE);

} KYVPExtension_DetectDevicesManually_Args;
#pragma pack(pop)

////////////////////////////////////////////////////////////////////////////////////////
/// API FUNCTIONS
////////////////////////////////////////////////////////////////////////////////////////

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_FirmwareUpdate(KYVPExtension_PCIInterface_FirmwareUpdate_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_CheckUpdateFile(KYVPExtension_PCIInterface_CheckUpdateFile_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_EventCallBackRegister(KYVPExtension_PCIInterface_EventCallBackRegister_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_EventCallBackUnregister(KYVPExtension_PCIInterface_EventCallBackUnregister_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_DirectHWReadReg(KYVPExtension_PCIInterface_DirectHWReadReg_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_DirectHWWriteReg(KYVPExtension_PCIInterface_DirectHWWriteReg_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_LockLink(KYVPExtension_PCIInterface_LockLink_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_UnlockLink(KYVPExtension_PCIInterface_UnlockLink_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_ReadLink(KYVPExtension_PCIInterface_ReadLink_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_WriteLink(KYVPExtension_PCIInterface_WriteLink_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_SendLinkEvent(KYVPExtension_PCIInterface_SendLinkEvent_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_GetLinkStatus(KYVPExtension_PCIInterface_LinkGetStatus_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_AuxDataCallback_Register(KYVPExtension_AuxDataCallback_Register_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_AuxDataCallback_Unregister(KYVPExtension_AuxDataCallback_Unregister_Args* _pArgs);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_RemoteDevice_DirectHWReadReg(KYVPExtension_RemoteDevice_DirectHWReadReg_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_RemoteDevice_DirectHWWriteReg(KYVPExtension_RemoteDevice_DirectHWWriteReg_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_RemoteDevice_SetImageDetails(KYVPExtension_RemoteDevice_SetImageDetails_Args* _pArgs);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_DataStream_Create(KYVPExtension_DataStream_Create_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_DataStream_Close(KYVPExtension_DataStream_Close_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_DataStream_Callback_Register(KYVPExtension_DataStream_Callback_Register_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_DataStream_Callback_Unregister(KYVPExtension_DataStream_Callback_Unregister_Args* _pArgs);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_Serial_Callback_Register(KYVPExtension_Serial_Callback_Register_Args* _pArgs);
KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_Serial_Callback_Unregister(KYVPExtension_Serial_Callback_Unregister_Args* _pArgs);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_RemoteDevice_GetParamsCollectionHandle(KYVPExtension_RemoteDevice_GetParamsCollectionHandle_Args* _pArgs);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_DetectDevicesManually(KYVPExtension_DetectDevicesManually_Args* _pArgs);

//////////////////////////////////////////////////////////////////////
/// KYVPEXTENSION API FUNCTIONS (VERSION 1)
//////////////////////////////////////////////////////////////////////

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_DirectHWReadReg_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle,
                                                                                         uint64_t _iBar,
                                                                                         uint64_t _iAddress,
                                                                                         void* _pBuffer,
                                                                                         size_t* _piSize);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_DirectHWWriteReg_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle,
                                                                                          uint64_t _iBar,
                                                                                          uint64_t _iAddress,
                                                                                          const void* _pBuffer,
                                                                                          size_t* _piSize);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_RemoteDevice_DirectHWReadReg_V1(KYVP_REMOTE_DEVICE_HANDLE _hRemoteDeviceHandle,
                                                                                         uint64_t _iAddress,
                                                                                         void* _pBuffer,
                                                                                         size_t* _piSize);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_RemoteDevice_DirectHWWriteReg_V1(KYVP_REMOTE_DEVICE_HANDLE _hRemoteDeviceHandle,
                                                                                          uint64_t _iAddress,
                                                                                          const void* _pBuffer,
                                                                                          size_t* _piSize);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_RemoteDevice_SetImageDetails_V1(KYVP_REMOTE_DEVICE_HANDLE _hRemoteDeviceHandle,
                                                                                         KYVP_DEVICE_IMAGE_DETAILS _imageDetails);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_LockLink_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle,
                                                                                          uint64_t _iLink);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_UnlockLink_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle,
                                                                                          uint64_t _iLink);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_ReadLink_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle,
                                                                                          uint64_t _iLink,
                                                                                          uint64_t _iAddress,
                                                                                          void* _pBuffer,
                                                                                          size_t* _piSize);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_WriteLink_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle,
                                                                                          uint64_t _iLink,
                                                                                          uint64_t _iAddress,
                                                                                          const void* _pBuffer,
                                                                                          size_t* _piSize);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_GetLinkStatus_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle,
                                                                                          uint64_t _iLink,
                                                                                          KYVP_LINK_STATUS_CMD _iInfoCmd,
                                                                                          KYVP_INFO_DATATYPE* _piType,
                                                                                          void* _pBuffer,
                                                                                          size_t* _piSize);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_AuxDataCallback_Register_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle,
                                                                                          KYVP_AUX_DATA_CALLBACK _pCallbackFunction,
                                                                                          void* _pUserContext);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_AuxDataCallback_Unregister_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle,
                                                                                          KYVP_AUX_DATA_CALLBACK _pCallbackFunction);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_DataStream_Create_V1(const char* _pszDevDataStreamID,
                                                                              KYVP_STREAM_BUFFERS_TYPE _eStreamBufferType,
                                                                              KYVP_STREAM_HANDLE* _phDSHandle);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_DataStream_Close_V1(KYVP_STREAM_HANDLE _hDSHandle);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_DataStream_Callback_Register_V1(KYVP_STREAM_HANDLE _hDSHandle,
                                                                                        KYVP_DATA_STREAM_CALLBACK _pCallbackFunction,
                                                                                        void* _pContext);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_DataStream_Callback_Unregister_V1(KYVP_STREAM_HANDLE _hDSHandle,
                                                                                          KYVP_DATA_STREAM_CALLBACK _pCallbackFunction);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_RemoteDevice_GetParamsCollectionHandle_V1(KYVP_REMOTE_DEVICE_HANDLE _hRemoteDeviceHandle,
                                                                                                   KYVP_COLLECTION_HANDLE* _phParamsCollectionHandle);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_DetectDevicesManually_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle,
                                                                                             KY_BOOL _bNoDevicesAccess,
                                                                                             KY_BOOL* _pbChanged,
                                                                                             DeviceDefinition* _pDeviceDefinitionArray,
                                                                                             size_t _uDeviceDefinitionArrayCount);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_EventCallBackRegister_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, KYVP_PCI_INTERFACE_EVENT_CALLBACK _pUserFunc, void* _pUserContext);

KYVP_EXTENSION_API KY_RESULT KYVP_CALLCONV KYVPExtension_PCIInterface_EventCallBackUnregister_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, KYVP_PCI_INTERFACE_EVENT_CALLBACK _pUserFunc);

#endif  // #ifndef KYVP_LIB_EXTENSION_H_
