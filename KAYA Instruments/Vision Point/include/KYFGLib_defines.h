#ifndef KYFG_LIB_DEFINES_H_
#define KYFG_LIB_DEFINES_H_

#ifdef __linux__
    #include <stddef.h>
#endif

#include "ky_lib_defines.h"
#include "ky_cxp_defines.h"
#include "KYFG_ErrorCodes.h"

#ifdef _MSC_VER
    #ifdef KYFG_EXPORTS
        #define KAYA_API __declspec(dllexport)
    #else
        #define KAYA_API __declspec(dllimport)
    #endif
#else
    #define KAYA_API
#endif

/**
 * \page APIDefinesAndMacros
 * \section APIHandles APIHandles
 * 	- FGHANDLE – API handle for Frame Grabber’s components functionality.<br>
 *    INVALID_FGHANDLE – Markup for invalid Frame Grabber API handle. 
 * 	- CAMHANDLE – API handle for Camera’s components functionality.<br>
 *    INVALID_CAMHANDLE – Markup for invalid Camera API handle.
 * 	- BUFFHANDLE – API handle for acquisition buffer components functionality.<br>
 *    INVALID_BUFFHANDLE – Markup for invalid Buffer API handle. 
 * 	- STREAM_HANDLE – API handle for acquisition stream components functionality.<br>
 *    INVALID_STREAMHANDLE – Markup for invalid Stream API handle.
 */

#ifdef __cplusplus
extern "C" {
#endif

_KY_TYPEDEF(uint32_t, FGHANDLE);
#define PRIFGHANDLE PRIX32

// read http://stackoverflow.com/questions/3025050/error-initializer-element-is-not-constant-when-trying-to-initialize-variable-w
// for a nice explanation about difference in *constant* definitions in C and C++ languages
// and why in case of C 'old good' #define is prefered over 'static const' (which is definetely prefered in case of C++)
#ifdef __cplusplus
static const FGHANDLE INVALID_FGHANDLE = (FGHANDLE(-1));
static const FGHANDLE NULL_FGHANDLE = (FGHANDLE(0));
#else
#define INVALID_FGHANDLE ((FGHANDLE)-1)
#define NULL_FGHANDLE = ((FGHANDLE)0);
#endif

typedef uint32_t CAMHANDLE;
#define PRICAMHANDLE PRIX32

#ifdef __cplusplus
static const CAMHANDLE INVALID_CAMHANDLE = (CAMHANDLE(-1));
static const CAMHANDLE NULL_CAMHANDLE = (CAMHANDLE(0));
#else
#define INVALID_CAMHANDLE ((CAMHANDLE)-1)
#define NULL_CAMHANDLE = ((CAMHANDLE)0);
#endif


typedef uint32_t STREAM_HANDLE;
#define PRISTREAM_HANDLE PRIX32
#ifdef __cplusplus
static const STREAM_HANDLE INVALID_BUFFHANDLE = (STREAM_HANDLE(-1));   // deprecated, use INVALID_STREAMHANDLE
static const STREAM_HANDLE INVALID_STREAMHANDLE = (STREAM_HANDLE(-1));
static const STREAM_HANDLE NULL_STREAMHANDLE = (STREAM_HANDLE(0));
#else
#define INVALID_BUFFHANDLE ((STREAM_HANDLE)-1)  // deprecated, use INVALID_STREAMHANDLE
#define INVALID_STREAMHANDLE ((STREAM_HANDLE)-1)
#define NULL_STREAMHANDLE ((STREAM_HANDLE)0)
#endif
typedef STREAM_HANDLE BUFFHANDLE;// for backward compatibility, now BUFFHANDLE is deprecated

typedef uint64_t STREAM_BUFFER_HANDLE;
#define PRISTREAM_BUFFER_HANDLE PRIX64
#ifdef __cplusplus
static const STREAM_BUFFER_HANDLE INVALID_STREAM_BUFFER_HANDLE = (STREAM_BUFFER_HANDLE(-1));
static const STREAM_BUFFER_HANDLE NULL_STREAM_BUFFER_HANDLE = (STREAM_BUFFER_HANDLE(0));
#else
#define INVALID_STREAM_BUFFER_HANDLE ((STREAM_BUFFER_HANDLE)-1)
#define NULL_STREAM_BUFFER_HANDLE ((STREAM_BUFFER_HANDLE)-0)
#endif

typedef uint64_t KYHANDLE;
#define PRIKY_HANDLE PRIX64
#ifdef __cplusplus
static const KYHANDLE INVALID_KY_HANDLE = (KYHANDLE(-1));
#else
#define INVALID_KY_HANDLE ((KYHANDLE)-1)
#endif

// Information about connected cameras

#ifdef __cplusplus
static const int KY_MAX_CAMERA_INFO_STRING_SIZE = 64;
#else
#define KY_MAX_CAMERA_INFO_STRING_SIZE	64
#endif


typedef struct _camera_info
{
    unsigned char       master_link;
    unsigned char       link_mask;
    CXP_LINK_SPEED      link_speed;
    uint32_t            stream_id;
    char                deviceVersion[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    char                deviceVendorName[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    char                deviceManufacturerInfo[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    char                deviceModelName[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    char                deviceID[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    char                deviceUserID[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    KYBOOL              outputCamera;
    KYBOOL              virtualCamera; //This parameter can be KYTRUE only in case of custom firmware implementations.
} KYFGCAMERA_INFO;

#pragma pack(push, 1)
/**
 * \page Structures
 * \section KYFGCAMERA_INFO2 KYFGCAMERA_INFO2
 * \ Camera configuration information. These fields are updated when the camera is connected using KYFG_CameraOpen2(). Changed values are being updated in runtime.
 * | Structure Field  			| Type									| Description 	|
 * | ---------------------  	| --------------						| -------- 		|
 * | version  					| uint32_t								| The version of the structure. For API versions prior to 6.2.0 must be set to 0. Since version 6.2.0, can be set to 1. 		|
 * | master_link				| uint8_t								| The master link channel. 		|
 * | link_mask					| uint8_t								| The mask of connected links. 		|
 * | link_speed					| \ref CXP_LINK_SPEED "CXP_LINK_SPEED"	| The mask of connected links. 		|
 * | deviceVersion				| char	[65]							| Camera version. 		|
 * | deviceVendorName			| char	[65]							| Vendor name. 		|
 * | deviceManufacturerInfo		| char	[65]							| Additional manufacturer info.		|
 * | deviceModelName			| char	[65]							| Camera model name.		|
 * | deviceID					| char	[65]							| Device id.		|
 * | deviceUserID				| char	[65]							| Device user id.		|
 * | outputCamera				| \ref KYBOOL "KYBOOL"					| KYTRUE if this is output camera, i.e. used for stream generation, KYFALSE otherwise.		|
 * | virtualCamera				| \ref KYBOOL "KYBOOL"					| KYTRUE if this is a virtual camera, i.e. internally implemented stream, which does not represent any physical camera, KYFALSE otherwise. This parameter can be KYTRUE only in the case of custom firmwareimplementations. |
 * | deviceFirmwareVersion		| char [65] 							| Camera firmware version. This field was introduced in API 6.2.0, and will only be filled by the function KYFG_CameraInfo2() if ‘version’ field is set to 1 |
*/
typedef struct _camera_info2
{
    uint32_t            version;  // Version of this structure definition, must be set to 0 or 1 by caller.

    uint8_t             master_link;
    uint8_t             link_mask;
    CXP_LINK_SPEED      link_speed;
    uint32_t            stream_id;
    char                deviceVersion[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    char                deviceVendorName[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    char                deviceManufacturerInfo[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    char                deviceModelName[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    char                deviceID[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    char                deviceUserID[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
    KYBOOL              outputCamera;
    KYBOOL              virtualCamera; //This parameter can be KYTRUE only in case of custom firmware implementations.

    // since version 1:
    char                deviceFirmwareVersion[KY_MAX_CAMERA_INFO_STRING_SIZE + 1];
}KYFGCAMERA_INFO2;
#pragma pack(pop)

#define PRIFGSTATUS PRIX32

// Simulator control
typedef enum _sim_control
{
    SIM_STOP,
    SIM_START, // Free running at specified frame rate
    SIM_PAUSE
}SIM_CONTROL;

// IO Structure 
typedef enum _io_source
{
    KY_LOGIC_0      = 0x00,
    KY_OPTO_0       = 0x01,
    KY_OPTO_1       = 0x02,
    KY_OPTO_2       = 0x03,
    KY_OPTO_3       = 0x04,

    KY_LVDS_0       = 0x05,
    KY_LVDS_1       = 0x06,
    KY_LVDS_2       = 0x07,
    KY_LVDS_3       = 0x08,

    KY_TTL_0        = 0x09,
    KY_TTL_1        = 0x0A,
    KY_TTL_2        = 0x0B,
    KY_TTL_3        = 0x0C,
    KY_TTL_4        = 0x0D,
    KY_TTL_5        = 0x0E,
    KY_TTL_6        = 0x0F,
    KY_TTL_7        = 0x10,

    KY_LVTTL_0      = 0x11,
    KY_LVTTL_1      = 0x12,
    KY_LVTTL_2      = 0x13,
    KY_LVTTL_3      = 0x14,

    KY_CXPTRIG_0    = 0x15,

    KY_CXP0_IO_0    = 0x20,
    KY_CXP0_IO_1    = 0x21,
    KY_CXP0_IO_2    = 0x22,
    KY_CXP0_IO_3    = 0x23,

    KY_LOGIC_1      = 0x3F
}KY_IO;

// Type of video source used by Chameleon simulator
typedef enum _VideoSourceType
{
    VIDEO_SOURCE_NONE    = -1,
    VIDEO_SOURCE_PATTERN = 0,
    VIDEO_SOURCE_FILE    = 1,
    VIDEO_SOURCE_FOLDER  = 2
} VIDEO_SOURCE_TYPE;

// Ramp pattern type
typedef enum _pattern_type
{
    PATTERN_XRAMP       = 0,
    PATTERN_XRAMP_COLOR = 1,
    PATTERN_YRAMP       = 2,
    PATTERN_YRAMP_COLOR = 3,
    PATTERN_XYRAMP      = 4,
    PATTERN_XYRAMP_COLOR= 5,
    PATTERN_FIXED       = 6
}PATTERN_TYPE;

// Valid flags for 'concurrency_mode' of KYFGLib_InitParameters
typedef enum _KYFGLIB_CONCURRENCY_FLAGS
{
    KYFGLIB_CONCURRENCY_SA_RESTART = 1, // Only valid in Linux, interrupts compatible with blocking calls in the same thread
}KYFGLIB_CONCURRENCY_FLAGS;

typedef void*(KYFG_CALLCONV *ky_aligned_malloc_t)(size_t size, size_t alignment);

#ifdef __cplusplus
static const int KY_MAX_INIT_VERSION = 2;
#else
#define KY_MAX_INIT_VERSION 2
#endif
#pragma pack(push, 1)
/**
 * \page Structures Structures
 * \section KYFGLib_InitParameters KYFGLib_InitParameters
 * | Structure Field  		| Type	 				| Description 	|
 * | ------------------ 	| ----------------- 	| ---------		|
 * | version 				| uint32_t 				| The version of this structure definition. Must be 1 or 2.|
 * | concurrency_mode		| uint32_t 				| Combination of KYFGLIB_CONCURRENCY_FLAGS, all unused bits must be set to 0.|
 * | logging_mode			| uint32_t 				| Reserved, must be set to 0.|
 * | noVideoStreamProcess	| KYBOOL 				| Since version 2 of structure. Use library without requesting video stream facilities, e.g for camera control only.|
 * | ky_aligned_malloc		| ky_aligned_malloc_t	| A user-defined function used by "KYFG_BufferAllocAndAnnounce()" to allocate acquisition buffers.|
 * \remarks 1.  See important notes regarding the usage of KYFGLIB_CONCURRENCY_FLAGS for Linux users.
 * \remarks 2.  Users who want this member to be considered by KYFGLib_Initialize() call. Must set the version value to 2 or highe.
 * \remarks 3.  Usage sample in the "KYFGLib_Example.c" - User-defined function used by "KYFG_BufferAllocAndAnnounce()" to allocate acquisition buffers.
 *
*/
typedef struct _KYFGLib_InitParameters
{
    uint32_t version    _KY_MEMBER_DEFAULT(1); // Version of this structure definition, must be between 1 and KY_MAX_INIT_VERSION

    // since version 1:
    uint32_t    concurrency_mode    _KY_MEMBER_DEFAULT(0);  // combination of KYFGLIB_CONCURRENCY_FLAGS, all unused bits must be set to 0
    uint32_t    logging_mode        _KY_MEMBER_DEFAULT(0);  // reserved, must be set to 0

    // since version 2:
    KYBOOL noVideoStreamProcess     _KY_MEMBER_DEFAULT(KYFALSE); // Use library without requesting video stream facilities, e.g for camera control only. This is possible for CXP grabbers only
}KYFGLib_InitParameters;
#pragma pack(pop)

#ifdef __cplusplus
static const uint64_t KYDEVICE_ERROR_FWUPDATE_REQUIRED = 0x0001;
static const uint64_t KYDEVICE_ERROR_SODIMM_REQUIRED = 0x0002;
#else
#define KYDEVICE_ERROR_FWUPDATE_REQUIRED  0x0001
#define KYDEVICE_ERROR_SODIMM_REQUIRED 0x0002
#endif

#ifdef __cplusplus
} // extern "C" {
#endif

#endif  // #ifndef KYFG_LIB_DEFINES_H_
