#ifndef KY_LIB_TYPEDEFS_H_
#define KY_LIB_TYPEDEFS_H_

#ifdef __cplusplus
static const int KY_MAX_CAMERAS = 16;
static const int KY_MAX_GRABBER_LINKS = 8;
#else
#define KY_MAX_CAMERAS 16
#define KY_MAX_GRABBER_LINKS 8
#endif


typedef uint8_t  KYBOOL;

// Parameter camera type
typedef enum _cam_property_type
{
    PROPERTY_TYPE_UNKNOWN   = -1,
    PROPERTY_TYPE_INT       = 0x00,
    PROPERTY_TYPE_BOOL      = 0x01,
    PROPERTY_TYPE_STRING    = 0x02,
    PROPERTY_TYPE_FLOAT     = 0x03,
    PROPERTY_TYPE_ENUM      = 0x04,
    PROPERTY_TYPE_COMMAND   = 0x05,
    PROPERTY_TYPE_REGISTER  = 0x06
}KY_CAM_PROPERTY_TYPE;

/**
 * \page Enumerations Enumerations
 * \section KY_DATA_TYPE KY_DATA_TYPE
 * Data types information
 * | Structure Field            | Value | Description | 
 * | ---------------            | ----- | ----------- | 
 * | KY_DATATYPE_UNKNOWN        |   0     | Unknown data type | 
 * | KY_DATATYPE_STRING         |   1     | NULL-terminated C string (ASCII encoded) | 
 * | KY_DATATYPE_STRINGLIST     |   2     | Concatenated INFO_DATATYPE_STRING list. End of list is signaled with an additional NULL | 
 * | KY_DATATYPE_INT16          |   3     | Signed 16 bit integer | 
 * | KY_DATATYPE_UINT16         |   4     | Unsigned 16 bit integer | 
 * | KY_DATATYPE_INT32          |   5     | Signed 32 bit integer | 
 * | KY_DATATYPE_UINT32         |   6     | Unsigned 32 bit integer | 
 * | KY_DATATYPE_INT64          |   7     | Signed 64 bit integer | 
 * | KY_DATATYPE_UINT64         |   8     | Unsigned 64 bit integer | 
 * | KY_DATATYPE_FLOAT64        |   9     | Signed 64 bit floating point number | 
 * | KY_DATATYPE_PTR            |   10     | Pointer type (void*). Size is platform dependent (32 bit on 32 bit platforms) | 
 * | KY_DATATYPE_BOOL8          |   11     | Boolean value occupying 8 bit. 0 for false and anything for true | 
 * | KY_DATATYPE_SIZET          |   12     | Platform dependent unsigned integer (32 bit on 32 bit platforms) | 
 * | KY_DATATYPE_BUFFER         |   13     | Like a INFO_DATATYPE_STRING but with arbitrary data and no NULL termination | 
 * | KY_DATATYPE_STREAM_HANDLE  |  1001    | STREAM_HANDLE | 
 * | KY_DATATYPE_CAMHANDLE      |  1002    | CAMHANDLE | 
*/
typedef enum _KY_DATA_TYPE
{
    KY_DATATYPE_UNKNOWN     = 0,    // Unknown data type
    KY_DATATYPE_STRING      = 1,    // NULL-terminated C string (ASCII encoded).
    KY_DATATYPE_STRINGLIST  = 2,    // Concatenated INFO_DATATYPE_STRING list. End of list is signaled with an additional NULL.
    KY_DATATYPE_INT16       = 3,    // Signed 16 bit integer
    KY_DATATYPE_UINT16      = 4,    // Unsigned 16 bit integer
    KY_DATATYPE_INT32       = 5,    // Signed 32 bit integer
    KY_DATATYPE_UINT32      = 6,    // Unsigned 32 bit integer
    KY_DATATYPE_INT64       = 7,    // Signed 64 bit integer
    KY_DATATYPE_UINT64      = 8,    // Unsigned 64 bit integer
    KY_DATATYPE_FLOAT64     = 9,    // Signed 64 bit floating point number.
    KY_DATATYPE_PTR         = 10,   // Pointer type (void*). Size is platform dependent (32 bit on 32 bit platforms).
    KY_DATATYPE_BOOL8       = 11,   // Boolean value occupying 8 bit. 0 for false and anything for true.
    KY_DATATYPE_SIZET       = 12,   // Platform dependent unsigned integer (32 bit on 32 bit platforms).
    KY_DATATYPE_BUFFER      = 13,   // Like a INFO_DATATYPE_STRING but with arbitrary data and no NULL termination.

    KY_DATATYPE_STREAM_HANDLE = 1001,   // a STREAM_HANDLE.
    KY_DATATYPE_CAMHANDLE = 1002,   // a CAMHANDLE.
}KY_DATA_TYPE;
/**
 * \page Enumerations Enumerations
 * \section KY_DEVICE_PROTOCOL KY_DEVICE_PROTOCOL
 * | Structure Field                 | Value | Description |
 * | ------------------------------  | ----- | ----------- |
 * | KY_DEVICE_PROTOCOL_CoaXPress    | 0x0    | Defines the device protocol as CoaXPress |
 * | KY_DEVICE_PROTOCOL_CLHS         | 0x1    | Defines the device protocol as CLHS |
 * | KY_DEVICE_PROTOCOL_GigE         | 0x2    | Defines the device protocol as GigE |
 * | KY_DEVICE_PROTOCOL_Mixed        | 0xFF    | Reserved for future use |
 * | KY_DEVICE_PROTOCOL_Unknown      | 0xFFFF| Unknown protocol |
*/
typedef enum _KY_DEVICE_PROTOCOL // BM: VP2: change to bitmask flags
{
    KY_DEVICE_PROTOCOL_CoaXPress = 0x0,
    KY_DEVICE_PROTOCOL_CLHS = 0x1,
    KY_DEVICE_PROTOCOL_GigE = 0x2,
    KY_DEVICE_PROTOCOL_Mixed = 0xFF,
    KY_DEVICE_PROTOCOL_Unknown = 0xFFFF// BM: VP2: this will be removed
}KY_DEVICE_PROTOCOL;



#ifdef __cplusplus
static const int KY_MAX_DEVICE_INFO_VERSION = 4;
#else
#define KY_MAX_DEVICE_INFO_VERSION 4
#endif

#pragma pack(push, 1)
/**
 * \page Structures Structures
 * \section KY_DEVICE_INFO KY_DEVICE_INFO
 * | Structure Field         | Type                     | Description     |
 * | ------------------      | -----------------        | -----------     |
 * | version                 | uint32_t                 | The version of this structure definition. On input must be no greater than KY_MAX_DEVICE_INFO_VERSION, value 0 is treated as 1. On output indicates version supported by current library     |
 * | szDeviceDisplayName     | char                     | The display name of the device     |
 * | nBus                    | int                      | Device PCIe bus                    |
 * | nSlot                   | int                      | Device PCIe slot                   |
 * | nFunction               | int                      | Device function                    |
 * | DevicePID               | uint32_t                 | Unique device ID                   |
 * | isVirtual               | \ref KYBOOL "KYBOOL"     | Indicates whether the device is virtual|
 * | m_Protocol              | uint8_t                  | Mask KY_DEVICE_STREAM_GRABBER indicates device that supports grabbing (input streams). Mask KY_DEVICE_STREAM_GENERATOR indicates device that supports generation (output streams).|
 * | m_Flags                 | \ref KY_DEVICE_PROTOCOL "KY_DEVICE_PROTOCOL"  |  For possible values see the table below. |
 * | DeviceGeneration        | uint32_t                 |  Device generation (1 or 2) |
*/
typedef struct _KY_DEVICE_INFO
{
    // Version of this structure
    // on input must be no greater than KY_MAX_DEVICE_INFO_VERSION, value 0 is treated as 1
    // on output indicates version supported by current library
    uint32_t version;

    // since version 1:
    char     szDeviceDisplayName[256];
    int      nBus;
    int      nSlot;
    int      nFunction;
    uint32_t DevicePID;
    KYBOOL   isVirtual;

    // following fields will be set by library only if caller initializes 'version' with number 2 (or more in library versions)
    // since version 2:
    // BM: VP2: 'm_Flags' should allow for more flags than 8
    uint8_t  m_Flags;   // mask KY_DEVICE_STREAM_GRABBER indicates device that supports grabbing (input streams).
                        // mask KY_DEVICE_STREAM_GENERATOR indicates device that supports generation (output streams).

    // since version 3:
    KY_DEVICE_PROTOCOL m_Protocol;

    // since version 4:
    uint32_t DeviceGeneration; // 1 or 2 (I or II)

    // following fields will be set by future library versions and only if caller initializes 'version' with number KY_MAX_DEVICE_INFO_VERSION+1 or more (when supported by library)
}KY_DEVICE_INFO;
#pragma pack(pop)

//
// CXP 2 Device events
//

#pragma pack(push, 1)
typedef struct _KY_CXPEVENT_PACK
{
    uint16_t nDataWords; // Number of data WORDS in 'eventDataWord'
    uint32_t eventDataWord[256]; // according to CXP standard, there can be maximum 1024 bytes of data
}KY_CXPEVENT_PACK;
#pragma pack(pop)

#pragma pack(push, 1)
typedef struct _BUFFER_DATA_CHUNK
{
    void* pBufferChunkMemory;   // Pointer to a buffer memory chunk.
    size_t bufferChunkSize;     // Size of buffer memory chunk, in bytes.
}BUFFER_DATA_CHUNK;
#pragma pack(pop)

#endif // #ifndef KY_LIB_TYPEDEFS_H_
