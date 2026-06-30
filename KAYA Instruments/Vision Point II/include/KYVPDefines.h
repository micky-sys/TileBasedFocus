#ifndef KYVP_LIB_TYPEDEFS_H_
#define KYVP_LIB_TYPEDEFS_H_

#include "KYFoundation.h"

// KYVP_CALLCONV definition
#ifdef __GNUC__
#define KYVP_CALLCONV
#ifndef KYVP_INLINE
#define KYVP_FORCE_INLINE_GCC __attribute__((always_inline))
#define KYVP_FORCE_INLINE_MSVC
#define KYVP_INLINE __inline
#endif //KYVP_INLINE
#elif defined(_MSC_VER)
#ifndef KYVP_INLINE
#define KYVP_FORCE_INLINE_GCC
#define KYVP_FORCE_INLINE_MSVC __forceinline
#define KYVP_INLINE
#endif //KYVP_INLINE

#define KYVP_CALLCONV __cdecl
#else
#define KYVP_CALLCONV
#endif

#ifdef __cplusplus
#define KYVP_MEMBER_DEFAULT(exp) = exp
#else
#define KYVP_MEMBER_DEFAULT(exp)
#endif

// KYVP_API definition

#ifdef __cplusplus
#define KYVP_EXTERNAL_C extern "C"
#else
#define KYVP_EXTERNAL_C
#endif

#define KYVP_EXTERNAL extern

#ifdef _MSC_VER
#ifdef KY_EXPORTS
#define KYVP_EXTERNAL_DLL __declspec(dllexport)
#else
#define KYVP_EXTERNAL_DLL __declspec(dllimport)
#endif
#else
#define KYVP_EXTERNAL_DLL
#endif

#define KYVP_API KYVP_EXTERNAL_C KYVP_EXTERNAL_DLL

//////////////////////////////////////////////////////////////////////
/// AUXILIARY DATA DEFINES
//////////////////////////////////////////////////////////////////////

#define KYVP_IO_CONTROLLER_MASKED_IO(masked_data)                   ((masked_data) & 0xFFFFFFFFFF)
#define KYVP_IO_CONTROLLER_MASKED_ENCODERS(masked_data)             (((masked_data) >> 40) & 0xF)
#define KYVP_IO_CONTROLLER_MASKED_TIMERS(masked_data)               (((masked_data) >> 44) & 0xFF)
#define KYVP_IO_CONTROLLER_MASKED_CAMERA_TRIGGERS(masked_data)      (((masked_data) >> 52) & 0xFF)
#define KYVP_IO_CONTROLLER_MASKED_TRIGGERS(masked_data)             (((masked_data) >> 60) & 0xF)

#define KYVP_AUX_MESSAGE_ID_IO_CONTROLLER                           11

#pragma pack(push, 1)
/**
 * @page KYVPExtension KYVPExtension
 * \section KYVP_IO_AUX_DATA KYVP_IO_AUX_DATA
 * Data portion interpretation of Auxiliary data structure, in case of callback issued by IO controller.
 * | Structure Field      | Type         | Description     |
 * | --------------       | -----        | ---------       |
 * | masked_data          | uint64_t     | Indicates the state of the I/O controller feature that can generate an event according to the table in the remarks.     |
 * | timestamp            | uint64_t     | Event timestamp in units of nano-seconds (nsec)     |
 * \remarks 1. Additional macros are provided to help extract specific IO controller elements:
     * \remarks 1.1. KYVP_IO_CONTROLLER_MASKED_IO - extract mask of GPIO triggers from IO Auxiliary masked data
     * \remarks 1.2. KYVP_IO_CONTROLLER_MASKED_ENCODERS - extract mask of encoders triggers from IO Auxiliary masked data
     * \remarks 1.3. KYVP_IO_CONTROLLER_MASKED_TIMERS - extract mask of timers triggers from IO Auxiliary masked data
     * \remarks 1.4. KYVP_IO_CONTROLLER_MASKED_CAMERA_TRIGGERS - extract mask of camera triggers from IO Auxiliary masked data
     * \remarks 1.5. KYVP_IO_CONTROLLER_MASKED_TRIGGERS - extract mask of stream triggers from IO Auxiliary masked data
 *
 * | Bit| Function                         | Bit     | Function                 | Bit     | Function                    |
 * | ---| -----------                      | --      | -----------              | ----    | ---------                   |
 * | 0     | OptoCoupled Input 0           | 22      | LVTTL 2                  | 43      | Encoder 3                   |
 * | 2     | OptoCoupled Input 1           | 23      | LVTTL 3                  | 44      | Timer 0                     |
 * | 3     | OptoCoupled Input 3           | 24      | LVTTL 4                  | 45      | Timer 1                     |
 * | 4     | OptoCoupled Input 4           | 25      | LVTTL 5                  | 46      | Timer 2                     |
 * | 5     | OptoCoupled Input 5           | 26      | LVTTL 6                  | 47      | Timer 3                     |
 * | 6     | OptoCoupled Input 6           | 27      | LVTTL 7                  | 48      | Timer 4                     |
 * | 7     | OptoCoupled Input 7           | 28      | OptoCoupled Output 0     | 49      | Timer 5                     |
 * | 8     | LVDS Input 0                  | 29      | OptoCoupled Output 1     | 50      | Timer 6                     |
 * | 9     | LVDS Input 1                  | 30      | OptoCoupled Output 2     | 51      | Timer 7                     |
 * | 10    | LVDS Input 2                  | 31      | OptoCoupled Output 3     | 52      | Camera Trigger 0            |
 * | 11    | LVDS Input 3                  | 32      | OptoCoupled Output 4     | 53      | Camera Trigger 1            |
 * | 12    | TTL0                          | 33      | OptoCoupled Output 5     | 54      | Camera Trigger 2            |
 * | 13    | TTL1                          | 34      | OptoCoupled Output 6     | 55      | Camera Trigger 3            |
 * | 14    | TTL2                          | 35      | OptoCoupled Output 7     | 56      | Camera Trigger 4            |
 * | 15    | TTL3                          | 36      | LVDS Output 0            | 57      | Camera Trigger 5            |
 * | 16    | TTL4                          | 37      | LVDS Output 1            | 58      | Camera Trigger 6            |
 * | 17    | TTL5                          | 38      | LVDS Output 2            | 59      | Camera Trigger 7            |
 * | 18    | TTL6                          | 39      | LVDS Output 3            | 60      | Acquisition Trigger 0       |
 * | 19    | TTL7                          | 40      | Encoder 0                | 61      | Acquisition Trigger 1       |
 * | 20    | LVTTL 0                       | 41      | Encoder 1                | 62      | Acquisition Trigger 2       |
 * | 21    | LVTTL 1                       | 42      | Encoder 2                | 63      | Acquisition Trigger 3       |
*/
typedef struct _KYVP_IO_AUX_DATA
{
    uint32_t version;

    // starting from version 1
    uint64_t masked_data;
    uint64_t timestamp;

}KYVP_IO_AUX_DATA;

/**
 * @page KYVPExtension KYVPExtension
 * \section KYVP_FRAME_AUX_DATA KYVP_FRAME_AUX_DATA
 * Data portion interpretation of Auxiliary data structure, in case of new frame arrival.
 * | Structure Field      | Type         | Description     |
 * | ------------         | ------       | -----------     |
 * | sequence_number      | uint32_t     | Sequential index of the frame within the allocated frame buffer     |
 * | timestamp            | uint64_t     | Frame arrival timestamp in units of nano-seconds (nsec)     |
*/
typedef struct _KYVP_FRAME_AUX_DATA
{
    uint32_t version;

    // starting from version 1
    uint32_t sequence_number;
    uint64_t timestamp;
    uint32_t reserved;

}KYVP_FRAME_AUX_DATA;

/**
 * @page KYVPExtension KYVPExtension
 * \section KYVP_AUX_DATA KYVP_AUX_DATA
 * Auxiliary data structure holding information of received Auxiliary message.
 * | Structure Field        | Type                                              | Description     |
 * | --------------         | ----                                              | ----------     |
 * | messageID              | uint32_t                                          | Unique ID of a received auxiliary data message. For possible values see Table 2.     |
 * | reserved               | \ref KYBOOL "KYBOOL"                              | Unique ID of a received auxiliary data message. For possible values see Table 2.     |
 * | dataSize               | size_t                                            | Size, in Bytes, of data delivered with Auxiliary message.    |
 * | u_data                 | union                                             | See remarks    |
 * | data                   | uint8_t [AUX_DATA_MAX_SIZE]                       | Data portion interpretation with no context.    |
 * | io_data                | \ref KYVP_IO_AUX_DATA "KYVP_IO_AUX_DATA"          | Data portion interpretation, in case of callback issued by IO controller    |
 * | frame_data             | \ref KYVP_FRAME_AUX_DATA "KYVP_FRAME_AUX_DATA"    | Data portion interpretation, in case of new frame arrival    |
 * \remarks Starting with version 6.0 the KYVP_AUX_DATA structure definition has changed. Usage of this structure requires client code changes -
            adding "u_data." to where these struct members are accessed, to avoid compilation warning about "nonstandard extension used:
            nameless struct/union".
 * | Message identifier                  | Description                                          |
 * | -------                             | --------                                             |
 * | KYVP_AUX_MESSAGE_ID_IO_CONTROLLER   | The message has been generated by IO Controller.     |
*/
typedef struct _KYVP_AUX_DATA
{
    uint8_t  version;

    // starting from version 1
    uint32_t messageID;

    union
    {
        KYVP_IO_AUX_DATA     io_data;                       // messageID == KYVP_AUX_MESSAGE_ID_IO_CONTROLLER
        KYVP_FRAME_AUX_DATA  frame_data;                    // reserved for internal usage
    } u_data;

    uint64_t    m_InterruptTimestampHw;
    uint64_t    m_InterruptTimestampChrono;

}KYVP_AUX_DATA;
#pragma pack(pop)

////////////////////////////////////////////////////////////////////////////////////
///  PCIINTERFACE EVENTS DEFINES
////////////////////////////////////////////////////////////////////////////////////

typedef enum _KYVP_PCIINTERFACE_EVENT_ID
{
    KYVP_PCIINTERFACE_EVENT_PROBE_ID                                = -1,
    KYVP_PCIINTERFACE_EVENT_TEMPERATURE_ID                          = 2,
    KYVP_PCIINTERFACE_EVENT_CXP2_HEARTBEAT_ID                       = 3,
    KYVP_PCIINTERFACE_EVENT_CXP2_EVENT_ID                           = 4,
    KYVP_PCIINTERFACE_EVENT_INVALID                                 = 0xFF
}KYVP_PCIINTERFACE_EVENT_ID; // KYDEVICE_EVENT_ID in VP1

typedef enum _KYVP_PCIINTERFACE_EVENT_TEMPERATURE_THRESHOLD_ID
{
    KYVP_PCIINTERFACE_EVENT_TEMPERATURE_THRESHOLD_NORMAL            = 0,
    KYVP_PCIINTERFACE_EVENT_TEMPERATURE_THRESHOLD_WARNING           = 1,
    KYVP_PCIINTERFACE_EVENT_TEMPERATURE_THRESHOLD_CRITICAL          = 2,
    KYVP_PCIINTERFACE_EVENT_TEMPERATURE_THRESHOLD_INVALID           = 0xFF
}KYVP_PCIINTERFACE_EVENT_TEMPERATURE_THRESHOLD_ID;

#pragma pack(push, 1)

#ifdef __cplusplus
static const int KYVP_CXP2_HEARTBEAT_VERSION = 1;
#else
#define KYVP_CXP2_HEARTBEAT_VERSION 1
#endif
typedef struct _KYVP_CXP2_HEARTBEAT
{
    uint32_t        version     KYVP_MEMBER_DEFAULT(KYVP_CXP2_HEARTBEAT_VERSION);
    uint32_t        uMasterHostConnectionID;                        // According to CXP 2.0 standard, holds the Host Connection ID of the Host connection connected to the Device Master connection
    uint64_t        uDeviceTime;                                    // The Device time is expressed in nanoseconds.
                                                                    // Note that this field is called "Device time" in the CXP standard
                                                                    // but in this API the notion "device" is used for PCI devices - grabbers and simulators
}KYVP_CXP2_HEARTBEAT;

#define KY_CXP_EVENT_MAX_DATA_SIZE 1024                     // According to CXP 2.0 standard

#ifdef __cplusplus
static const int KYVP_CXP2_EVENT_VERSION = 1;
#else
#define KYVP_CXP2_EVENT_VERSION 1
#endif
typedef struct _KYVP_CXP2_EVENT
{
    uint32_t        version     KYVP_MEMBER_DEFAULT(KYVP_CXP2_EVENT_VERSION);
    uint32_t        uMasterHostConnectionID;                       // According to CXP 2.0 standard, holds the Host Connection ID of the Host connection connected to the Device Master connection
    uint8_t         uTag;                                          // 8 bit tag. Incremented for each new Event packet.
    uint16_t        uDataSize;                                     // The number of event data words, Maximum size = CXP_EVENT_MAX_DATA_SIZE bytes according to CXP 2.0 standard
    uint32_t        uDataWordsArr[KY_CXP_EVENT_MAX_DATA_SIZE / 4]; // 'dataSize' words with one or more event messages
}KYVP_CXP2_EVENT;

#pragma pack(pop)

/////////////////////////////////////////////////////////////////////////////////////////////////////
/// CXP standard
/////////////////////////////////////////////////////////////////////////////////////////////////////

/**
 * @page KYVPDefines Defines
 * @section KYVP_TAP_GEOMETRY KYVP_TAP_GEOMETRY
 * | Enumerator                                             | Value  | Description                                              |
 * | ------------------------                               | ------ | ----------------                                         |
 * | TAP_GEOMETRY_1X_1Y                                     | 0x00   | Geometry_1X_1Y: 1 X 1Y tap geometry                      |
 * | TAP_GEOMETRY_1X_2YE                                    | 0x41   | Geometry_1X_2YE: 1 X 2YE tap geometry                    |
 */
typedef enum _KYVP_TAP_GEOMETRY
{
    TAP_GEOMETRY_1X_1Y = 0x00,
    TAP_GEOMETRY_1X_2YE = 0x41,
    TAP_GEOMETRY_INVALID = 0xFF

}KYVP_TAP_GEOMETRY;

// Camera CoaXPress link available speed values
typedef enum _KYVP_CXP_LINK_SPEED
{
    KYVP_CXP_LINK_SPEED_CXP0 = 0,
    KYVP_CXP_LINK_SPEED_CXP1 = 0x28,
    KYVP_CXP_LINK_SPEED_CXP2 = 0x30,
    KYVP_CXP_LINK_SPEED_CXP3 = 0x38,
    KYVP_CXP_LINK_SPEED_CXP5 = 0x40,
    KYVP_CXP_LINK_SPEED_CXP6 = 0x48,
    KYVP_CXP_LINK_SPEED_CXP10 = 0x50,
    KYVP_CXP_LINK_SPEED_CXP12 = 0x58,
}KYVP_CXP_LINK_SPEED;

#pragma pack(push, 1)
/** @brief   A buffer data chunk. */
typedef struct _KYVP_BUFFER_DATA_CHUNK
{
    void* pBufferChunkMemory;       // Pointer to a buffer memory chunk.
    size_t bufferChunkSize;         // Size of buffer memory chunk, in bytes.
}KYVP_BUFFER_DATA_CHUNK;
#pragma pack(pop)

#ifdef __cplusplus
static const uint64_t KYVP_PCI_INTERFACE_STREAM_UNKNOWN     = 0x0;
static const uint64_t KYVP_PCI_INTERFACE_STREAM_GRABBER     = 0x1;
static const uint64_t KYVP_PCI_INTERFACE_STREAM_GENERATOR   = 0x2;
#else
#define KYVP_PCI_INTERFACE_STREAM_UNKNOWN                   0x0
#define KYVP_PCI_INTERFACE_STREAM_GRABBER                   0x1
#define KYVP_PCI_INTERFACE_STREAM_GENERATOR                 0x2
#endif

/////////////////////////////////////////////////////////////////////////////////////////////////////
/// KYVP_PCI_INTERFACE_PROTOCOL
/////////////////////////////////////////////////////////////////////////////////////////////////////

typedef enum _KYVP_PCI_INTERFACE_PROTOCOL
{
    KYVP_PCI_INTERFACE_PROTOCOL_CoaXPress                   = 0x0,
    KYVP_PCI_INTERFACE_PROTOCOL_CLHS                        = 0x1,
    KYVP_PCI_INTERFACE_PROTOCOL_PCI                         = 0x2,
    KYVP_PCI_INTERFACE_PROTOCOL_Mixed                       = 0xFF
}KYVP_PCI_INTERFACE_PROTOCOL;

/////////////////////////////////////////////////////////////////////////////////////////////////////
/// KYVP_PCI_INTERFACE_SLOT
/////////////////////////////////////////////////////////////////////////////////////////////////////

#pragma pack(push, 1)
typedef struct _KYVP_PCI_INTERFACE_SLOT
{
    uint8_t m_uBus : 8;
    uint8_t m_uSlot : 5;
    uint8_t m_uFunction : 3; // function is vendor specific value
                             // value 7 indicates virtual (software emulated) device
}KYVP_PCI_INTERFACE_SLOT;
#pragma pack(pop)

/////////////////////////////////////////////////////////////////////////////////////////////////////
/// KYVP_PCI_CONFIG
/////////////////////////////////////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVP_PCI_CONFIG_VERSION = 1;
#else
#define KYVP_PCI_CONFIG_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVP_PCI_CONFIG
{
    uint8_t m_uGeneration   KYVP_MEMBER_DEFAULT(UINT8_MAX);         // PCI generation
    uint8_t m_uLanes        KYVP_MEMBER_DEFAULT(UINT8_MAX);         // Number of PCI lanes 

}KYVP_PCI_CONFIG;
#pragma pack(pop)

/////////////////////////////////////////////////////////////////////////////////////////////////////
/// KYVP_BOARD_INFO
/////////////////////////////////////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVP_MAX_BOARD_INFO_VERSION = 1;
#else
#define KYVP_MAX_BOARD_INFO_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVP_BOARD_INFO
{
    // Version of this structure
    // on input must be greater than 0 and no greater than KYVP_MAX_BOARD_INFO_VERSION
    // on output indicates version supported by current library
    uint8_t                     m_uVersion              KYVP_MEMBER_DEFAULT(KYVP_MAX_BOARD_INFO_VERSION);

    KYVP_PCI_INTERFACE_SLOT     m_Slot                  KYVP_MEMBER_DEFAULT({});
    uint32_t                    m_uDevicePID            KYVP_MEMBER_DEFAULT(UINT32_MAX);
    KYVP_PCI_INTERFACE_PROTOCOL m_Protocol              KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_PROTOCOL_Mixed);
    uint8_t                     m_uDeviceGeneration     KYVP_MEMBER_DEFAULT(UINT8_MAX);                                     // 1, 2, 3... (marketed as I, II, III...)
    uint8_t                     m_uRevision             KYVP_MEMBER_DEFAULT(UINT8_MAX);                                     // Board revision number
    uint32_t                    m_uPhysicalLinks        KYVP_MEMBER_DEFAULT(UINT32_MAX);
    KYVP_PCI_CONFIG             m_ActivePCIConfig       KYVP_MEMBER_DEFAULT({});
    KYVP_PCI_CONFIG             m_MinimalPCIConfig      KYVP_MEMBER_DEFAULT({});
    uint64_t                    m_uFlags                KYVP_MEMBER_DEFAULT(0);                                             // mask KYVP_DEVICE_STREAM_GRABBER indicates device that supports grabbing (input streams).
    // mask KYVP_DEVICE_STREAM_GENERATOR indicates device that supports generation (output streams).
}KYVP_BOARD_INFO;
#pragma pack(pop)

/////////////////////////////////////////////////////////////////////////////////////////////////////
/// KYVP_PCI_INTERFACE_INFO
/////////////////////////////////////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int MAX_PCI_INTERFACE_LINKS = 8;
#else
#define MAX_PCI_INTERFACE_LINKS 8
#endif

#ifdef __cplusplus
static const int KYVP_PCI_INTERFACE_INFO_VERSION = 1;
#else
#define KYVP_PCI_INTERFACE_INFO_VERSION 1
#endif

#ifdef __cplusplus
static const int KY_MAX_PCI_INTERFACE_INFO_STRING_SIZE KYVP_MEMBER_DEFAULT(256);
#else
#define KY_MAX_PCI_INTERFACE_INFO_STRING_SIZE 256
#endif

#pragma pack(push, 1)
typedef struct _KYVP_PCI_INTERFACE_INFO
{
    // Version of this structure
    // on input must be greater than 0 and no greater than KYVP_MAX_PCI_INTERFACE_INFO_VERSION
    // on output indicates version supported by current library
    uint8_t             m_uVersion                                                          KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_INFO_VERSION);

    // Since version 1:
    char                m_szDeviceDisplayName[KY_MAX_PCI_INTERFACE_INFO_STRING_SIZE + 1]    KYVP_MEMBER_DEFAULT("");
    uint32_t            m_uSerialNumber                                                     KYVP_MEMBER_DEFAULT(UINT32_MAX);
    KYVP_BOARD_INFO     m_KYVP_BoardInfo;                                                                                           // DO NOT confuse with "struct BOARD_INFO" used in flash!!!
    KY_BOOL             m_bIsVirtual                                                        KYVP_MEMBER_DEFAULT(KY_FALSE);

    // following fields will be set by future library versions and only if caller initializes 'version' 
    // with number KYVP_MAX_PCI_INTERFACE_INFO_VERSION + 1 or more (when supported by library)
}KYVP_PCI_INTERFACE_INFO;
#pragma pack(pop)

/////////////////////////////////////////////////////////////////////////////////////////////////////
/// KYVP_DEVICE_INFO
/////////////////////////////////////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KY_MAX_DEVICE_INFO_STRING_SIZE KYVP_MEMBER_DEFAULT(64);
#else
#define KY_MAX_DEVICE_INFO_STRING_SIZE 64
#endif

#ifdef __cplusplus
static const int KYVP_DEVICE_INFO_VERSION = 1;
#else
#define KYVP_DEVICE_INFO_VERSION 1
#endif

// This struct describes a detected camera
// An array of this struct instances is kept in shared memory to coordinate this information
// between processes that share the same grabber
#pragma pack(push, 1)
typedef struct _KYVP_DEVICE_INFO
{
    uint32_t             m_uVersion        KYVP_MEMBER_DEFAULT(KYVP_DEVICE_INFO_VERSION); // Version of this structure definition, must be set to 0 by caller.

    // Since version 1:
    uint8_t              m_uMasterLink     KYVP_MEMBER_DEFAULT((uint8_t)-1);                                // Grabber's link to which Camera's master link is connected
    KY_PROCID            m_ProcId          KYVP_MEMBER_DEFAULT(0);                                          // Process ID of a process that currently has this Camera(Device) open
    uint8_t              m_uLinkMask       KYVP_MEMBER_DEFAULT(0);
    int                  m_nMaxDeviceLinks KYVP_MEMBER_DEFAULT(0);
    int                  m_vecDeviceConnectionTopology[MAX_PCI_INTERFACE_LINKS] KYVP_MEMBER_DEFAULT({-1});  // Each index into this vector designates a link of a device
                                                                                                            // and value at that index shows to what PCI link it is connected
                                                                                                            // note that only fisrt element is "-1" initialized
                                                                                                            // the full initialization is done in the code which instantiates KYVP_DEVICE_INFO

    KYVP_CXP_LINK_SPEED  m_linkSpeed                                                    KYVP_MEMBER_DEFAULT(KYVP_CXP_LINK_SPEED_CXP0);
    uint32_t             m_uStreamId[256]                                               KYVP_MEMBER_DEFAULT({});
    KY_BOOL              m_uValidStreamId[256]                                          KYVP_MEMBER_DEFAULT({});
    char                 m_szDeviceVersion[KY_MAX_DEVICE_INFO_STRING_SIZE + 1]          KYVP_MEMBER_DEFAULT({});
    char                 m_szDeviceVendorName[KY_MAX_DEVICE_INFO_STRING_SIZE + 1]       KYVP_MEMBER_DEFAULT({});
    char                 m_szDeviceManufacturerInfo[KY_MAX_DEVICE_INFO_STRING_SIZE + 1] KYVP_MEMBER_DEFAULT({});
    char                 m_szDeviceModelName[KY_MAX_DEVICE_INFO_STRING_SIZE + 1]        KYVP_MEMBER_DEFAULT({});
    char                 m_szDeviceID[KY_MAX_DEVICE_INFO_STRING_SIZE + 1]               KYVP_MEMBER_DEFAULT({});
    char                 m_szDeviceUserID[KY_MAX_DEVICE_INFO_STRING_SIZE + 1]           KYVP_MEMBER_DEFAULT({});
    char                 m_szDeviceFirmwareVersion[KY_MAX_DEVICE_INFO_STRING_SIZE + 1]  KYVP_MEMBER_DEFAULT({});
    KY_BOOL              m_bOutputCamera  KYVP_MEMBER_DEFAULT(KY_FALSE);
    KY_BOOL              m_bVirtualCamera KYVP_MEMBER_DEFAULT(KY_FALSE); // This parameter can be KYTRUE only in case of custom firmware implementations.
    uint32_t             m_uLinkConfig    KYVP_MEMBER_DEFAULT(0);        // Needed to restore this member of LINK_INFO of master's link when camera detection is simulated
}KYVP_DEVICE_INFO;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYVP_DEVICE_IMAGE_DETAILS_VERSION = 1;
#else
#define KYVP_DEVICE_IMAGE_DETAILS_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVP_DEVICE_IMAGE_DETAILS
{
    uint32_t                    m_uVersion                                              KYVP_MEMBER_DEFAULT(KYVP_DEVICE_IMAGE_DETAILS_VERSION);       // Version of this structure definition, must be set by caller.

    // Since version 1:
    uint32_t                    m_uWidth                                                KYVP_MEMBER_DEFAULT(UINT32_MAX);
    uint32_t                    m_uHeight                                               KYVP_MEMBER_DEFAULT(UINT32_MAX);
    uint32_t                    m_uPixelFormat                                          KYVP_MEMBER_DEFAULT(UINT32_MAX);
    char                        m_szPixelFormatName[KY_MAX_DEVICE_INFO_STRING_SIZE + 1] KYVP_MEMBER_DEFAULT("");
    char                        m_szTapGeometryType[KY_MAX_DEVICE_INFO_STRING_SIZE + 1] KYVP_MEMBER_DEFAULT("");
}KYVP_DEVICE_IMAGE_DETAILS;
#pragma pack(pop)

/////////////////////////////////////////////////////////////////////////////////////////////////////
/// KYVP_BUFFER_INFO
/////////////////////////////////////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVP_BUFFER_INFO_VERSION = 1;
#else
#define KYVP_BUFFER_INFO_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYVP_BUFFER_INFO
{
    uint32_t                    m_uVersion              KYVP_MEMBER_DEFAULT(KYVP_BUFFER_INFO_VERSION);         // Version of this structure definition, must be set to 0 by caller.

    // Since version 1:
    const uint64_t*             m_puFrameId             KYVP_MEMBER_DEFAULT(nullptr);
    const uint64_t*             m_puBufferId            KYVP_MEMBER_DEFAULT(nullptr);
    const uint64_t*             m_puTimestampNS         KYVP_MEMBER_DEFAULT(nullptr);
    const double*               m_puInstantFPS          KYVP_MEMBER_DEFAULT(nullptr);
    const void*                 m_pBase                 KYVP_MEMBER_DEFAULT(nullptr);
    const size_t*               m_puSize                KYVP_MEMBER_DEFAULT(nullptr);
    const void*                 m_pContext              KYVP_MEMBER_DEFAULT(nullptr);
    const size_t*               m_puWidth               KYVP_MEMBER_DEFAULT(nullptr);
    const size_t*               m_puHeight              KYVP_MEMBER_DEFAULT(nullptr);
    const uint64_t*             m_puPixelFormat         KYVP_MEMBER_DEFAULT(nullptr);
    const uint64_t*             m_puTimestampNS_HW      KYVP_MEMBER_DEFAULT(nullptr);
    const uint64_t*             m_puTimestampNS_CHRONO  KYVP_MEMBER_DEFAULT(nullptr);

}KYVP_BUFFER_INFO;
#pragma pack(pop)

#endif // #ifndef KYVP_LIB_TYPEDEFS_H_
