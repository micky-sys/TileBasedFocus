#ifndef KYVP_LIB_TL_H_
#define KYVP_LIB_TL_H_

#include "GenTL_v1_5.h"
#include "KYVPDefines.h"

#ifdef _MSC_VER
#ifdef KY_TL_EXPORTS
#define KYVPTL_EXTERNAL_DLL __declspec(dllexport)
#else
#define KYVPTL_EXTERNAL_DLL __declspec(dllimport)
#endif
#else
#define KYVPTL_EXTERNAL_DLL
#endif

#define KYVPTL_API          KYVP_EXTERNAL_C KYVPTL_EXTERNAL_DLL
#define KYVPTL_VAR          KYVP_EXTERNAL KYVPTL_EXTERNAL_DLL

#if defined(_MSC_VER)
#  pragma warning(push)
#  pragma warning(disable:4514) /* unreferenced inline function has been removed */
#endif


/**
 * @page KYVPLibTL KYVPLibTL Library
 * @brief Overview of the KYVPLibTL library.
 *
 * @subpage KYVPLibTL_Introduction
 * @subpage KYVPLibTL_Handles
 * @subpage KYVPLibTL_Structs
 * @subpage KYVPLibTL_Functions
 */

/**
 * @page KYVPLibTL_Introduction KYVPLibTL Introduction
 * @includedoc KYVPTL_library.html
 */

 /**
  * @page KYVPLibTL_Functions KYVPLibTL Functions
  * @brief Functional areas of the KYVPLibTL library.
  *
  * - @subpage KYVPLibTL_Functions_System    
  * - @subpage KYVPLibTL_Functions_Interface 
  * - @subpage KYVPLibTL_Functions_Stream    
  * - @subpage KYVPLibTL_Functions_Event     
  */

/**
 * @page KYVPLibTL_Functions_System KYVPLibTL System Functions
 * @brief Functions in System module of the KYVPLibTL library.
 */

/**
 * @page KYVPLibTL_Functions_Interface KYVPLibTL Interface Functions
 * @brief Functions in Interface module of the KYVPLibTL library.
 */

/**
 * @page KYVPLibTL_Functions_Stream KYVPLibTL Stream Functions
 * @brief Functions in Stream module of the KYVPLibTL library.
 */

/**
 * @page KYVPLibTL_Functions_Event KYVPLibTL Event Functions
 * @brief Functions in Event module of the KYVPLibTL library.
 */

 ///////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////
 /// ENUMS
 ///////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////

#define KYVP_CUSTOM_ID_DEBUG 10000

#ifdef __cplusplus
#ifdef __GNUC__
#define GENTL_NS(_entry_) GenTL::_entry_
#endif
#ifdef _MSC_VER
#define GENTL_NS(_entry_) GenTL::##_entry_
#endif

#else
#define GENTL_NS(_entry_) _entry_
#endif

/**
 * @page KYVPDefines Defines
 * @section KYVP_INFO_DATATYPE KYVP_INFO_DATATYPE
 * Defines the data type possible for the various Info functions. The data type itself may define
    its size. For buffer or string types the piSize parameter must be used to query the actual
    amount of data being written.
 * | Enumerator                         | Value     | Description                                                                       |
 * | -------------                      | ------    | --------                                                                          |
 * | KYVP_INFO_DATATYPE_UNKNOWN         | 0         | Unknown data type. This value is never returned from a function but can be used to initialize the variable to inquire the type. |
 * | KYVP_INFO_DATATYPE_STRING          | 1         | 0-terminated C string (encoding according to the TL_INFO_CHAR_ENCODING info command) |
 * | KYVP_INFO_DATATYPE_STRINGLIST      | 2         | Concatenated INFO_DATATYPE_STRING list. End of list is signaled with an additional 0 |
 * | KYVP_INFO_DATATYPE_INT16           | 3         | Signed 16 bit integer.                                                               |
 * | KYVP_INFO_DATATYPE_UINT16          | 4         | Unsigned 16 bit integer                                                              |
 * | KYVP_INFO_DATATYPE_INT32           | 5         | Signed 32 bit integer                                                                |
 * | KYVP_INFO_DATATYPE_UINT32          | 6         | Unsigned 32 bit integer                                                              |
 * | KYVP_INFO_DATATYPE_INT64           | 7         | Signed 64 bit integer                                                                |
 * | KYVP_INFO_DATATYPE_UINT64          | 8         | Unsigned 64 bit integer                                                              |
 * | KYVP_INFO_DATATYPE_FLOAT64         | 9         | Signed 64 bit floating point number                                                  |
 * | KYVP_INFO_DATATYPE_PTR             | 10        | Pointer type (void*). Size is platform dependent (32 bit on 32 bit platforms)        |
 * | KYVP_INFO_DATATYPE_BOOL8           | 11        | Boolean value occupying 8 bit. 0 for false and anything for true                     |
 * | KYVP_INFO_DATATYPE_SIZET           | 12        | Platform dependent unsigned integer (32 bit on 32 bit platforms)                     |
 * | KYVP_INFO_DATATYPE_BUFFER          | 13        | Like a INFO_DATATYPE_STRING but with arbitrary data and no 0 termination.            |
 * | KYVP_INFO_DATATYPE_PTRDIFF         | 14        | The type ptrdiff_t is a type that can hold the result of subtracting two pointers.   |
 *
*/
typedef enum _KYVP_INFO_DATATYPE
{
    KYVP_INFO_DATATYPE_UNKNOWN      = GENTL_NS(INFO_DATATYPE_UNKNOWN),
    KYVP_INFO_DATATYPE_STRING       = GENTL_NS(INFO_DATATYPE_STRING),
    KYVP_INFO_DATATYPE_STRINGLIST   = GENTL_NS(INFO_DATATYPE_STRINGLIST),
    KYVP_INFO_DATATYPE_INT16        = GENTL_NS(INFO_DATATYPE_INT16),
    KYVP_INFO_DATATYPE_UINT16       = GENTL_NS(INFO_DATATYPE_UINT16),
    KYVP_INFO_DATATYPE_INT32        = GENTL_NS(INFO_DATATYPE_INT32),
    KYVP_INFO_DATATYPE_UINT32       = GENTL_NS(INFO_DATATYPE_UINT32),
    KYVP_INFO_DATATYPE_INT64        = GENTL_NS(INFO_DATATYPE_INT64),
    KYVP_INFO_DATATYPE_UINT64       = GENTL_NS(INFO_DATATYPE_UINT64),
    KYVP_INFO_DATATYPE_FLOAT64      = GENTL_NS(INFO_DATATYPE_FLOAT64),
    KYVP_INFO_DATATYPE_PTR          = GENTL_NS(INFO_DATATYPE_PTR),
    KYVP_INFO_DATATYPE_BOOL8        = GENTL_NS(INFO_DATATYPE_BOOL8),
    KYVP_INFO_DATATYPE_SIZET        = GENTL_NS(INFO_DATATYPE_SIZET),
    KYVP_INFO_DATATYPE_BUFFER       = GENTL_NS(INFO_DATATYPE_BUFFER),
    KYVP_INFO_DATATYPE_PTRDIFF      = GENTL_NS(INFO_DATATYPE_PTRDIFF),
    KYVP_INFO_DATATYPE_INVALID      = 0xFFFF
} KYVP_INFO_DATATYPE;

typedef enum _KYVP_TL_CHAR_ENCODING
{
    KYVP_TL_CHAR_ENCODING_ASCII     = GENTL_NS(TL_CHAR_ENCODING_ASCII),
    KYVP_TL_CHAR_ENCODING_UTF8      = GENTL_NS(TL_CHAR_ENCODING_UTF8),
    KYVP_TL_CHAR_ENCODING_INVALID   = 0xFFFF
} KYVP_TL_CHAR_ENCODING;

/**
 * @page KYVPDefines Defines
 * @section KYVP_TL_INFO_CMD KYVP_TL_INFO_CMD
 * | Field name                        | Data type      | Description                                                                   |
 * | ---------------                   | -------------- | --------------------                                                          |
 * | KYVP_TL_INFO_CMD_ID               | STRING         | Unique ID identifying a GenTL                                                 |
 * | KYVP_TL_INFO_CMD_VENDOR           | STRING         | Vendor name                                                                   |
 * | KYVP_TL_INFO_CMD_MODEL            | STRING         | Model name                                                                    |
 * | KYVP_TL_INFO_CMD_VERSION          | STRING         | Producer version                                                              |
 * | KYVP_TL_INFO_CMD_TLTYPE           | STRING         | Transport layer technology that is supported                                  |
 * | KYVP_TL_INFO_CMD_NAME             | STRING         | File name including extension of the library                                  |
 * | KYVP_TL_INFO_CMD_PATHNAME         | STRING         | Full path including file name and extension of the library                    |
 * | KYVP_TL_INFO_CMD_DISPLAYNAME      | STRING         | User readable name                                                            |
 * | KYVP_TL_INFO_CMD_CHAR_ENCODING    | INT32          | The char encoding                                                             |
 * | KYVP_TL_INFO_CMD_GENTL_VER_MAJOR  | INT32          | Major version number of GenTL Standard Version this Producer complies with.   |
 * | KYVP_TL_INFO_CMD_GENTL_VER_MINOR  | INT32          | Minor version number of GenTL Standard Version this Producer complies with.   |
 * | KYVP_TL_INFO_CMD_API_VER_MAJOR    | INT32          | Software API major version.                                                   |
 * | KYVP_TL_INFO_CMD_API_VER_MINOR    | INT32          | Software API minor version.                                                   |
 * | KYVP_TL_INFO_CMD_API_VER_PATCH    | INT32          | Software API patch version.                                                   |
 * | KYVP_TL_INFO_CMD_API_VER_BUILD    | INT32          | Software API build (revision) version.                                        |
 * | KYVP_TL_INFO_CMD_API_VER_ALPHA    | BOOL           | True if alpha build.                                                          |
 * | KYVP_TL_INFO_CMD_API_VER_BETA     | BOOL           | True if beta build.                                                           |
 * | KYVP_TL_INFO_CMD_API_VER_RC       | BOOL           | True if release candidate build.                                              |
 */
typedef enum _KYVP_TL_INFO_CMD
{
    KYVP_TL_INFO_CMD_ID                 = GENTL_NS(TL_INFO_ID),
    KYVP_TL_INFO_CMD_VENDOR             = GENTL_NS(TL_INFO_VENDOR),
    KYVP_TL_INFO_CMD_MODEL              = GENTL_NS(TL_INFO_MODEL),
    KYVP_TL_INFO_CMD_VERSION            = GENTL_NS(TL_INFO_VERSION),
    KYVP_TL_INFO_CMD_TLTYPE             = GENTL_NS(TL_INFO_TLTYPE),
    KYVP_TL_INFO_CMD_NAME               = GENTL_NS(TL_INFO_NAME),
    KYVP_TL_INFO_CMD_PATHNAME           = GENTL_NS(TL_INFO_PATHNAME),
    KYVP_TL_INFO_CMD_DISPLAYNAME        = GENTL_NS(TL_INFO_DISPLAYNAME),
    KYVP_TL_INFO_CMD_CHAR_ENCODING      = GENTL_NS(TL_INFO_CHAR_ENCODING),
    KYVP_TL_INFO_CMD_GENTL_VER_MAJOR    = GENTL_NS(TL_INFO_GENTL_VER_MAJOR),
    KYVP_TL_INFO_CMD_GENTL_VER_MINOR    = GENTL_NS(TL_INFO_GENTL_VER_MINOR),
    KYVP_TL_INFO_CMD_API_VER_MAJOR      = GENTL_NS(TL_INFO_CUSTOM_ID + 1),
    KYVP_TL_INFO_CMD_API_VER_MINOR      = GENTL_NS(TL_INFO_CUSTOM_ID + 2),
    KYVP_TL_INFO_CMD_API_VER_PATCH      = GENTL_NS(TL_INFO_CUSTOM_ID + 3),
    KYVP_TL_INFO_CMD_API_VER_BUILD      = GENTL_NS(TL_INFO_CUSTOM_ID + 4),
    KYVP_TL_INFO_CMD_API_VER_ALPHA      = GENTL_NS(TL_INFO_CUSTOM_ID + 5),
    KYVP_TL_INFO_CMD_API_VER_BETA       = GENTL_NS(TL_INFO_CUSTOM_ID + 6),
    KYVP_TL_INFO_CMD_API_VER_RC         = GENTL_NS(TL_INFO_CUSTOM_ID + 7),
    KYVP_TL_INFO_CMD_INVALID            = 0xFFFF
} KYVP_TL_INFO_CMD;

/**
 * @page KYVPDefines Defines
 * @section KYVP_TL_INFO_CMD KYVP_TL_INFO_CMD
 * | Field name                                         | Data type      | Description                                   |
 * | ---------------                                    | -------------- | --------------------                          |
 * | KYVP_INTERFACE_INFO_CMD_ID                         | STRING         | Unique ID of the interface.                   |
 * | KYVP_INTERFACE_INFO_CMD_DISPLAYNAME                | STRING         | User readable name of the interface.          |
 * | KYVP_INTERFACE_INFO_CMD_TLTYPE                     | STRING         | Transport layer technology that is supported  |
 * | KYVP_INTERFACE_INFO_CMD_PCI_SLOT                   | STRING         | Info about interface PCI slot                 |
 * | KYVP_INTERFACE_INFO_CMD_KYVP_PCI_INTERFACE_INFO    | PTR            | Pointer to KYVP_PCI_INTERFACE_INFO struct     |
*/
typedef enum _KYVP_INTERFACE_INFO_CMD
{
    KYVP_INTERFACE_INFO_CMD_ID                          = GENTL_NS(INTERFACE_INFO_ID),
    KYVP_INTERFACE_INFO_CMD_DISPLAYNAME                 = GENTL_NS(INTERFACE_INFO_DISPLAYNAME),
    KYVP_INTERFACE_INFO_CMD_TLTYPE                      = GENTL_NS(INTERFACE_INFO_TLTYPE),
    KYVP_INTERFACE_INFO_CMD_PCI_SLOT                    = GENTL_NS(INTERFACE_INFO_CUSTOM_ID + 1),                          // STRING     Info about interface PCI slot.
    KYVP_INTERFACE_INFO_CMD_KYVP_PCI_INTERFACE_INFO     = GENTL_NS(INTERFACE_INFO_CUSTOM_ID + 2),           // PTR        Pointer to KYVP_PCI_INTERFACE_INFO struct
    KYVP_INTERFACE_INFO_CMD_INVALID                     = 0xFFFF
} KYVP_INTERFACE_INFO_CMD;

typedef enum _KYVP_DEVICE_ACCESS_FLAGS
{
    KYVP_DEVICE_ACCESS_FLAGS_UNKNOWN    = GENTL_NS(DEVICE_ACCESS_UNKNOWN),
    KYVP_DEVICE_ACCESS_FLAGS_NONE       = GENTL_NS(DEVICE_ACCESS_NONE),
    KYVP_DEVICE_ACCESS_FLAGS_READONLY   = GENTL_NS(DEVICE_ACCESS_READONLY),
    KYVP_DEVICE_ACCESS_FLAGS_CONTROL    = GENTL_NS(DEVICE_ACCESS_CONTROL),
    KYVP_DEVICE_ACCESS_FLAGS_EXCLUSIVE  = GENTL_NS(DEVICE_ACCESS_EXCLUSIVE)
} KYVP_DEVICE_ACCESS_FLAGS;

typedef enum _KYVP_DEVICE_ACCESS_STATUS
{
    KYVP_DEVICE_ACCESS_STATUS_UNKNOWN           = GENTL_NS(DEVICE_ACCESS_STATUS_UNKNOWN),
    KYVP_DEVICE_ACCESS_STATUS_READWRITE         = GENTL_NS(DEVICE_ACCESS_STATUS_READWRITE),
    KYVP_DEVICE_ACCESS_STATUS_READONLY          = GENTL_NS(DEVICE_ACCESS_STATUS_READONLY),
    KYVP_DEVICE_ACCESS_STATUS_NOACCESS          = GENTL_NS(DEVICE_ACCESS_STATUS_NOACCESS),
    KYVP_DEVICE_ACCESS_STATUS_BUSY              = GENTL_NS(DEVICE_ACCESS_STATUS_BUSY),
    KYVP_DEVICE_ACCESS_STATUS_OPEN_READWRITE    = GENTL_NS(DEVICE_ACCESS_STATUS_OPEN_READWRITE),
    KYVP_DEVICE_ACCESS_STATUS_OPEN_READONLY     = GENTL_NS(DEVICE_ACCESS_STATUS_OPEN_READONLY)
} KYVP_DEVICE_ACCESS_STATUS;

/**
 * @page KYVPDefines Defines
 * @section KYVP_DEVICE_INFO_CMD KYVP_DEVICE_INFO_CMD
 * | Field name                                 | Data type         | Description                                   |
 * | ---------------                            | --------------    | --------------------                          |
 * | KYVP_DEVICE_INFO_CMD_ID                    | STRING            | Unique ID of the device                                                     |
 * | KYVP_DEVICE_INFO_CMD_VENDOR                | STRING            | Device vendor name                                                          |
 * | KYVP_DEVICE_INFO_CMD_MODEL                 | STRING            | Device model name                                                           |
 * | KYVP_DEVICE_INFO_CMD_TLTYPE                | STRING            | Transport layer technology that is supported                                |
 * | KYVP_DEVICE_INFO_CMD_DISPLAYNAME           | STRING            | User readable name of the device                                            |
 * | KYVP_DEVICE_INFO_CMD_ACCESS_STATUS         | INT32             | Gets the access status the Producer has on the device                       |
 * | KYVP_DEVICE_INFO_CMD_USER_DEFINED_NAME     | STRING            | String containing the user defined name of the device                       |
 * | KYVP_DEVICE_INFO_CMD_SERIAL_NUMBER         | STRING            | Serial number of the device in string format                                |
 * | KYVP_DEVICE_INFO_CMD_VERSION               | STRING            | Device version in string format                                             |
 * | KYVP_DEVICE_INFO_CMD_TIMESTAMP_FREQUENCY   | UINT64            | Tick frequency of the device's timestamp counter in ticks per second        |
 * | KYVP_DEVICE_INFO_CMD_KYVP_DEVICE_INFO      | PTR               | Pointer to KYVP_DEVICE_INFO struct. The pointed data must be treated as read-only: modifying its contents is undefined behavior |
 */
typedef enum _KYVP_DEVICE_INFO_CMD
{
    KYVP_DEVICE_INFO_CMD_ID                     = GENTL_NS(DEVICE_INFO_ID),
    KYVP_DEVICE_INFO_CMD_VENDOR                 = GENTL_NS(DEVICE_INFO_VENDOR),
    KYVP_DEVICE_INFO_CMD_MODEL                  = GENTL_NS(DEVICE_INFO_MODEL),
    KYVP_DEVICE_INFO_CMD_TLTYPE                 = GENTL_NS(DEVICE_INFO_TLTYPE),
    KYVP_DEVICE_INFO_CMD_DISPLAYNAME            = GENTL_NS(DEVICE_INFO_DISPLAYNAME),
    KYVP_DEVICE_INFO_CMD_ACCESS_STATUS          = GENTL_NS(DEVICE_INFO_ACCESS_STATUS),
    KYVP_DEVICE_INFO_CMD_USER_DEFINED_NAME      = GENTL_NS(DEVICE_INFO_USER_DEFINED_NAME),
    KYVP_DEVICE_INFO_CMD_SERIAL_NUMBER          = GENTL_NS(DEVICE_INFO_SERIAL_NUMBER),
    KYVP_DEVICE_INFO_CMD_VERSION                = GENTL_NS(DEVICE_INFO_VERSION),
    KYVP_DEVICE_INFO_CMD_TIMESTAMP_FREQUENCY    = GENTL_NS(DEVICE_INFO_TIMESTAMP_FREQUENCY),
    KYVP_DEVICE_INFO_CMD_KYVP_DEVICE_INFO       = GENTL_NS(DEVICE_INFO_CUSTOM_ID + 1),        // PTR Pointer to KYVP_DEVICE_INFO struct. 
    //The pointed data must be treated as read-only: modifying its contents is undefined behavior
    KYVP_DEVICE_INFO_CMD_INVALID                = 0xFFFF
} KYVP_DEVICE_INFO_CMD;

/**
 * @page KYVPDefines Defines
 * @section KYVP_ACQ_START_FLAGS KYVP_ACQ_START_FLAGS
 * | Enumerator                                 | Value             | Description                                   |
 * | ---------------                            | --------------    | --------------------                          |
 * | KYVP_ACQ_START_FLAGS_DEFAULT               | 0                 | Default behavior                              |
 */
typedef enum _KYVP_ACQ_START_FLAGS
{
    KYVP_ACQ_START_FLAGS_DEFAULT    = GENTL_NS(ACQ_START_FLAGS_DEFAULT)
} KYVP_ACQ_START_FLAGS;

/**
 * @page KYVPDefines Defines
 * @section KYVP_ACQ_STOP_FLAGS KYVP_ACQ_STOP_FLAGS
 * | Enumerator                                 | Value             | Description                                   |
 * | ---------------                            | --------------    | --------------------                          |
 * | KYVP_ACQ_STOP_FLAGS_DEFAULT                | 0                 | Stops the acquisition engine when the currently running tasks like filling a buffer are completed                             |
 * | KYVP_ACQ_STOP_FLAGS_KILL                   | 1                 | Stop the acquisition engine immediately. In case this results in a partially filled buffer the Producer will return the buffer through the regular mechanism to the user, indicating through the info function of that buffer that this buffer is not complete
 */
typedef enum _KYVP_ACQ_STOP_FLAGS
{
    KYVP_ACQ_STOP_FLAGS_DEFAULT     = GENTL_NS(ACQ_STOP_FLAGS_DEFAULT),
    KYVP_ACQ_STOP_FLAGS_KILL        = GENTL_NS(ACQ_STOP_FLAGS_KILL)
} KYVP_ACQ_STOP_FLAGS;

typedef enum _KYVP_ACQ_QUEUE_TYPE
{
    KYVP_ACQ_QUEUE_TYPE_INPUT_TO_OUTPUT     = GENTL_NS(ACQ_QUEUE_INPUT_TO_OUTPUT),
    KYVP_ACQ_QUEUE_OUTPUT_DISCARD           = GENTL_NS(ACQ_QUEUE_OUTPUT_DISCARD),
    KYVP_ACQ_QUEUE_ALL_TO_INPUT             = GENTL_NS(ACQ_QUEUE_ALL_TO_INPUT),
    KYVP_ACQ_QUEUE_UNQUEUED_TO_INPUT        = GENTL_NS(ACQ_QUEUE_UNQUEUED_TO_INPUT),
    KYVP_ACQ_QUEUE_ALL_DISCARD              = GENTL_NS(ACQ_QUEUE_ALL_DISCARD),
    KYVP_ACQ_QUEUE_INVALID                  = 0xFFFF
} KYVP_ACQ_QUEUE_TYPE;

/**
 * @page KYVPDefines Defines
 * @section KYVP_STREAM_INFO_CMD KYVP_STREAM_INFO_CMD
 * Defines the data type possible for the various Info functions. The data type itself may define
    its size. For buffer or string types the piSize parameter must be used to query the actual
    amount of data being written.
 * | Enumerator                                 | Value     | Type      | Description                                      |
 * | ------------------------                   | ------    | --------- | ----------------                                 |
 * | KYVP_STREAM_INFO_CMD_ID                    | 0         | STRING    | Unique ID of the data stream      |
 * | KYVP_STREAM_INFO_CMD_NUM_DELIVERED         | 1         | UINT64    | Number of delivered buffers since last acquisition start                                             |
 * | KYVP_STREAM_INFO_CMD_NUM_UNDERRUN          | 2         | UINT64    | Number of lost frames due to queue underrun                                                          |
 * | KYVP_STREAM_INFO_CMD_NUM_ANNOUNCED         | 3         | SIZET     | Number of announced buffers                                                                          |
 * | KYVP_STREAM_INFO_CMD_NUM_QUEUED            | 4         | SIZET     | Number of buffers in the input pool plus the buffer(s) currently being filled                        |
 * | KYVP_STREAM_INFO_CMD_NUM_AWAIT_DELIVERY    | 5         | SIZET     | Number of buffers in the output buffer queue                                                         |
 * | KYVP_STREAM_INFO_CMD_NUM_STARTED           | 6         | UINT64    | Number of frames started in the acquisition engine                                                   |
 * | KYVP_STREAM_INFO_CMD_PAYLOAD_SIZE          | 7         | SIZET     | Size of the expected data in bytes                                                                   |
 * | KYVP_STREAM_INFO_CMD_IS_GRABBING           | 8         | BOOL      | Flag indicating whether the acquisition engine is started or not                                     |
 * | KYVP_STREAM_INFO_CMD_DEFINES_PAYLOADSIZE   | 9         | BOOL      | Flag indicating that this data stream defines a payload size independent from the remote device      |
 * | KYVP_STREAM_INFO_CMD_TLTYPE                | 10        | STRING    | Transport layer technology that is supported                                                         |
 * | KYVP_STREAM_INFO_CMD_NUM_CHUNKS_MAX        | 11        | SIZET     | Maximum number of chunks to be expected in a buffer                                                  |
 * | KYVP_STREAM_INFO_CMD_BUF_ANNOUNCE_MIN      | 12        | SIZET     | Minimum number of buffers to announce                                                                |
 * | KYVP_STREAM_INFO_CMD_BUF_ALIGNMENT         | 13        | SIZET     | Alignment size in bytes of the buffer passed to DSAnnounceBuffer                                     |
 */
typedef enum _KYVP_STREAM_INFO_CMD
{
    KYVP_STREAM_INFO_CMD_ID                     = GENTL_NS(STREAM_INFO_ID),
    KYVP_STREAM_INFO_CMD_NUM_DELIVERED          = GENTL_NS(STREAM_INFO_NUM_DELIVERED),
    KYVP_STREAM_INFO_CMD_NUM_UNDERRUN           = GENTL_NS(STREAM_INFO_NUM_UNDERRUN),
    KYVP_STREAM_INFO_CMD_NUM_ANNOUNCED          = GENTL_NS(STREAM_INFO_NUM_ANNOUNCED),
    KYVP_STREAM_INFO_CMD_NUM_QUEUED             = GENTL_NS(STREAM_INFO_NUM_QUEUED),
    KYVP_STREAM_INFO_CMD_NUM_AWAIT_DELIVERY     = GENTL_NS(STREAM_INFO_NUM_AWAIT_DELIVERY),
    KYVP_STREAM_INFO_CMD_NUM_STARTED            = GENTL_NS(STREAM_INFO_NUM_STARTED),
    KYVP_STREAM_INFO_CMD_PAYLOAD_SIZE           = GENTL_NS(STREAM_INFO_PAYLOAD_SIZE),
    KYVP_STREAM_INFO_CMD_IS_GRABBING            = GENTL_NS(STREAM_INFO_IS_GRABBING),
    KYVP_STREAM_INFO_CMD_DEFINES_PAYLOADSIZE    = GENTL_NS(STREAM_INFO_DEFINES_PAYLOADSIZE),
    KYVP_STREAM_INFO_CMD_TLTYPE                 = GENTL_NS(STREAM_INFO_TLTYPE),
    KYVP_STREAM_INFO_CMD_NUM_CHUNKS_MAX         = GENTL_NS(STREAM_INFO_NUM_CHUNKS_MAX),
    KYVP_STREAM_INFO_CMD_BUF_ANNOUNCE_MIN       = GENTL_NS(STREAM_INFO_BUF_ANNOUNCE_MIN),
    KYVP_STREAM_INFO_CMD_BUF_ALIGNMENT          = GENTL_NS(STREAM_INFO_BUF_ALIGNMENT),
    KYVP_STREAM_INFO_CMD_INVALID                = 0xFFFF
} KYVP_STREAM_INFO_CMD;

/**
 * @page KYVPDefines Defines
 * @section KYVP_BUFFER_INFO_CMD KYVP_BUFFER_INFO_CMD
 * | Enumerator                                             | Value             | Type      | Description                                      |
 * | ------------------------                               | ------            | --------- | ----------------                                 |
 * | KYVP_BUFFER_INFO_CMD_BASE                              | 0                 | PTR       | Base address of the buffer memory as passed to the KYVPLibTL_DSAnnounceBuffer function                                                                  |
 * | KYVP_BUFFER_INFO_CMD_SIZE                              | 1                 | SIZET     | Size of the buffer in bytes.                                                                                                                            |
 * | KYVP_BUFFER_INFO_CMD_USER_PTR                          | 2                 | PTR       | Private data pointer casted to an integer provided at buffer announcement using KYVPLibTL_DSAnnounceBuffer or KYVPLibTL_DSAllocAndAnnounceBuffer        |
 * | KYVP_BUFFER_INFO_CMD_TIMESTAMP                         | 3                 | UINT64    | Timestamp the buffer was acquired                                                                                                                       |
 * | KYVP_BUFFER_INFO_CMD_NEW_DATA                          | 4                 | BOOL      | Flag to indicate that the buffer contains new data since the last delivery                                                                              |
 * | KYVP_BUFFER_INFO_CMD_IS_QUEUED                         | 5                 | BOOL      | If this flag is set to true the buffer is in the input pool, the buffer is currently being filled or the buffer is in the output buffer queue. In case this value is true the buffer is owned by the GenTL Producer and it can not be revoked      |
 * | KYVP_BUFFER_INFO_CMD_IS_ACQUIRING                      | 6                 | BOOL      | Flag to indicate that the buffer is currently being filled with data                                                                                                                                                                               |
 * | KYVP_BUFFER_INFO_CMD_IS_INCOMPLETE                     | 7                 | BOOL      | Flag to indicate that a buffer was filled but an error occurred during that process                                                                                                                                                                |
 * | KYVP_BUFFER_INFO_CMD_TLTYPE                            | 8                 | STRING    | Transport layer technology that is supported                                                                                                                                                                                                       |
 * | KYVP_BUFFER_INFO_CMD_SIZE_FILLED                       | 9                 | SIZET     | Number of bytes written into the buffer the last time it has been filled. This value is reset to 0 when the buffer is placed into the Input Buffer Pool.If the buffer is incomplete (such as if there are missing packets), only thenumber of bytes successfully writtento the buffer are reported. If the bufferis complete, the number equals to thesize reported throughKYVP_BUFFER_INFO_DATA_SIZE |
 * | KYVP_BUFFER_INFO_CMD_WIDTH                             | 10                | SIZET     | Width of the data in the buffer in number of pixels. This information refers for example to the width entry in the GigE Vision image stream data leader. For other technologies this is to be implemented accordingly                                                                                                                                                                                 |
 * | KYVP_BUFFER_INFO_CMD_HEIGHT                            | 11                | SIZET     | Height of the data in the buffer in number of pixels as configured. For variable size images this is the maximum height of the buffer. For example this information refers to the height entry in the GigE Vision image stream data leader. For other technologies this is to be implemented  accordingly                                                                                             |
 * | KYVP_BUFFER_INFO_CMD_XOFFSET                           | 12                | SIZET     | XOffset of the data in the buffer in number of pixels from the image origin to handle areas of interest. This information refers for example to the information provided in the GigE Vision image stream data leader. For other technologies this is to be implemented accordingly                                                                                                                    |
 * | KYVP_BUFFER_INFO_CMD_YOFFSET                           | 13                | SIZET     | YOffset of the data in the buffer in number of lines from the image origin to handle areas of interest. This information refers for example to the information provided in the GigE Vision image stream data leader. For other technologies this is to be implemented accordingly                                                                                                                     |
 * | KYVP_BUFFER_INFO_CMD_XPADDING                          | 14                | SIZET     | XPadding of the data in the buffer in number of bytes. This information refers for example to the information provided in the GigE Vision image stream data leader. For other technologies this is may be implemented accordingly                                                                                                                                                                     |
 * | KYVP_BUFFER_INFO_CMD_YPADDING                          | 15                | SIZET     | YPadding of the data in the buffer in number of bytes. This information refers for example to the information provided in the GigE Vision image stream data leader. For other thechnologies this may be implemented accordingly                                                                                                                                                                       |
 * | KYVP_BUFFER_INFO_CMD_FRAMEID                           | 16                | UINT64    | A sequentially incremented number of the frame. This information refers for example to the information provided in the GigE Vision image stream block id. For other technologies this is to be  implemented accordingly. The wrap around of this number is transportation technology dependent.                                                                                                       |
 * | KYVP_BUFFER_INFO_CMD_IMAGEPRESENT                      | 17                | BOOL      | Flag to indicate if the current data in the buffer contains image data                                                                                                                                                                                                                                                                                                                                |
 * | KYVP_BUFFER_INFO_CMD_IMAGEOFFSET                       | 18                | SIZET     | Offset of the image data from the beginning of the delivered buffer in bytes                                                                                                                                                                                                                                                                                                                          |
 * | KYVP_BUFFER_INFO_CMD_PAYLOADTYPE                       | 19                | SIZET     | Payload type of the data                                                                                                                                                                                                                                                                                                                                                                              |
 * | KYVP_BUFFER_INFO_CMD_PIXELFORMAT                       | 20                | UINT64    | Pixelformat of the data                                                                                                                                                                                                                                                                                                                                                                               |
 * | KYVP_BUFFER_INFO_CMD_PIXELFORMAT_NAMESPACE             | 21                | UINT64    |                                                                                                                                                                                                                                                                                                                                                                                                       |
 * | KYVP_BUFFER_INFO_CMD_DELIVERED_IMAGEHEIGHT             | 22                | SIZET     | The number of lines in the current buffer as delivered by the transport mechanism                                  |
 * | KYVP_BUFFER_INFO_CMD_DELIVERED_CHUNKPAYLOADSIZE        | 23                | SIZET     | This information refers for example to the information provided in the GigE Vision image stream data trailer       |
 * | KYVP_BUFFER_INFO_CMD_CHUNKLAYOUTID                     | 24                | UINT64    | This information refers for example to the information provided in the GigE Vision image stream data leader. The chunk layout id serves as an indicator that the chunk layout has changed and the application should reparse the chunk layout in the buffer. When a chunk layout (availability or position of individual chunks) changes since the last buffer delivered by the device through the same stream, the device must change the chunk layout id. As long as the chunk layout remains stable, the camera must keep the chunk layout id intact. When switching back to a layout, which was already used before, the camera can use the same id again or use a new id. A chunk layout id value of 0 is invalid. It is reserved for use by cameras not supporting the layout id functionality. The algorithm used to compute the chunk layout id is left as quality of implementation. For other technologies this is to be implemented accordingly. |
 * | KYVP_BUFFER_INFO_CMD_FILENAME                          | 25                | STRING    | Filename in case the payload contains a file  |
 * | KYVP_BUFFER_INFO_CMD_PIXEL_ENDIANNESS                  | 26                | INT32     | Endianness of the multi-byte pixel data in the buffer
 * | KYVP_BUFFER_INFO_CMD_DATA_SIZE                         | 27                | SIZET     | Size of the data intended to be written to the buffer last time it has been filled                                                                                             |
 * | KYVP_BUFFER_INFO_CMD_TIMESTAMP_NS                      | 28                | UINT64    | Timestamp the buffer was acquired, in units of 1 ns (1 000 000 000 ticks per second)                                                                                           |
 * | KYVP_BUFFER_INFO_CMD_DATA_LARGER_THAN_BUFFER           | 29                | BOOL      | If this values is set to true it indicates that the payload transferred would not fit into the announced buffer and that therefore only parts of the payload or no payload     |
 * | KYVP_BUFFER_INFO_CMD_CONTAINS_CHUNKDATA                | 30                | BOOL      | If this values is set to true it indicates that the payload transferred contains chunk data                                                                                   |
 * | KYVP_BUFFER_INFO_CMD_BUFFERID                          | 1001              | UINT64    | Buffer ID in buffers queue                      |
 * | KYVP_BUFFER_INFO_CMD_INSTANT_FPS                       | 1002              | FLOAT64   | Instant FPS counter                             |
 * | KYVP_BUFFER_INFO_CMD_KYVP_BUFFER_INFO                  | 1003              | PTR       | Pointer to KYVP_BUFFER_INFO struct              |
 * | KYVP_BUFFER_INFO_CMD_TIMESTAMP_NS_CHRONO               | 1004              | UINT64    | Timestamp the interrupt for this buffer was received (std::chrono::steady_clock), in units of 1 ns (1 000 000 000 ticks per second)              |
 * | KYVP_BUFFER_INFO_CMD_TIMESTAMP_NS_HW                   | 10001             | UINT64    | Timestamp the hw interrupt for this buffer was received (hardware register), in units of 1 ns (1 000 000 000 ticks per second). Used for debugging purposes; activated when the corresponding INTERNAL setting is enabled              |
 */
typedef enum _KYVP_BUFFER_INFO_CMD
{
    KYVP_BUFFER_INFO_CMD_BASE                           = GENTL_NS(BUFFER_INFO_BASE),
    KYVP_BUFFER_INFO_CMD_SIZE                           = GENTL_NS(BUFFER_INFO_SIZE),
    KYVP_BUFFER_INFO_CMD_USER_PTR                       = GENTL_NS(BUFFER_INFO_USER_PTR),
    KYVP_BUFFER_INFO_CMD_TIMESTAMP                      = GENTL_NS(BUFFER_INFO_TIMESTAMP),
    KYVP_BUFFER_INFO_CMD_NEW_DATA                       = GENTL_NS(BUFFER_INFO_NEW_DATA),
    KYVP_BUFFER_INFO_CMD_IS_QUEUED                      = GENTL_NS(BUFFER_INFO_IS_QUEUED),
    KYVP_BUFFER_INFO_CMD_IS_ACQUIRING                   = GENTL_NS(BUFFER_INFO_IS_ACQUIRING),
    KYVP_BUFFER_INFO_CMD_IS_INCOMPLETE                  = GENTL_NS(BUFFER_INFO_IS_INCOMPLETE),
    KYVP_BUFFER_INFO_CMD_TLTYPE                         = GENTL_NS(BUFFER_INFO_TLTYPE),
    KYVP_BUFFER_INFO_CMD_SIZE_FILLED                    = GENTL_NS(BUFFER_INFO_SIZE_FILLED),
    KYVP_BUFFER_INFO_CMD_WIDTH                          = GENTL_NS(BUFFER_INFO_WIDTH),
    KYVP_BUFFER_INFO_CMD_HEIGHT                         = GENTL_NS(BUFFER_INFO_HEIGHT),
    KYVP_BUFFER_INFO_CMD_XOFFSET                        = GENTL_NS(BUFFER_INFO_XOFFSET),
    KYVP_BUFFER_INFO_CMD_YOFFSET                        = GENTL_NS(BUFFER_INFO_YOFFSET),
    KYVP_BUFFER_INFO_CMD_XPADDING                       = GENTL_NS(BUFFER_INFO_XPADDING),
    KYVP_BUFFER_INFO_CMD_YPADDING                       = GENTL_NS(BUFFER_INFO_YPADDING),
    KYVP_BUFFER_INFO_CMD_FRAMEID                        = GENTL_NS(BUFFER_INFO_FRAMEID),
    KYVP_BUFFER_INFO_CMD_IMAGEPRESENT                   = GENTL_NS(BUFFER_INFO_IMAGEPRESENT),
    KYVP_BUFFER_INFO_CMD_IMAGEOFFSET                    = GENTL_NS(BUFFER_INFO_IMAGEOFFSET),
    KYVP_BUFFER_INFO_CMD_PAYLOADTYPE                    = GENTL_NS(BUFFER_INFO_PAYLOADTYPE),
    KYVP_BUFFER_INFO_CMD_PIXELFORMAT                    = GENTL_NS(BUFFER_INFO_PIXELFORMAT),
    KYVP_BUFFER_INFO_CMD_PIXELFORMAT_NAMESPACE          = GENTL_NS(BUFFER_INFO_PIXELFORMAT_NAMESPACE),
    KYVP_BUFFER_INFO_CMD_DELIVERED_IMAGEHEIGHT          = GENTL_NS(BUFFER_INFO_DELIVERED_IMAGEHEIGHT),
    KYVP_BUFFER_INFO_CMD_DELIVERED_CHUNKPAYLOADSIZE     = GENTL_NS(BUFFER_INFO_DELIVERED_CHUNKPAYLOADSIZE),
    KYVP_BUFFER_INFO_CMD_CHUNKLAYOUTID                  = GENTL_NS(BUFFER_INFO_CHUNKLAYOUTID),
    KYVP_BUFFER_INFO_CMD_FILENAME                       = GENTL_NS(BUFFER_INFO_FILENAME),
    KYVP_BUFFER_INFO_CMD_PIXEL_ENDIANNESS               = GENTL_NS(BUFFER_INFO_PIXEL_ENDIANNESS),
    KYVP_BUFFER_INFO_CMD_DATA_SIZE                      = GENTL_NS(BUFFER_INFO_DATA_SIZE),
    KYVP_BUFFER_INFO_CMD_TIMESTAMP_NS                   = GENTL_NS(BUFFER_INFO_TIMESTAMP_NS),
    KYVP_BUFFER_INFO_CMD_DATA_LARGER_THAN_BUFFER        = GENTL_NS(BUFFER_INFO_DATA_LARGER_THAN_BUFFER),
    KYVP_BUFFER_INFO_CMD_CONTAINS_CHUNKDATA             = GENTL_NS(BUFFER_INFO_CONTAINS_CHUNKDATA),
    KYVP_BUFFER_INFO_CMD_BUFFERID                       = GENTL_NS(BUFFER_INFO_CUSTOM_ID) + 1,
    KYVP_BUFFER_INFO_CMD_INSTANT_FPS                    = GENTL_NS(BUFFER_INFO_CUSTOM_ID) + 2,
    KYVP_BUFFER_INFO_CMD_KYVP_BUFFER_INFO               = GENTL_NS(BUFFER_INFO_CUSTOM_ID) + 3,
    KYVP_BUFFER_INFO_CMD_TIMESTAMP_NS_CHRONO            = GENTL_NS(BUFFER_INFO_CUSTOM_ID) + 4,

    // For debugging purposes (activated when the corresponding setting is enabled):
    KYVP_BUFFER_INFO_CMD_TIMESTAMP_NS_HW                = KYVP_CUSTOM_ID_DEBUG + 1,

    KYVP_BUFFER_INFO_CMD_INVALID                        = 0xFFFF
} KYVP_BUFFER_INFO_CMD;

typedef enum _KYVP_BUFFER_PART_INFO_CMD
{
    KYVP_BUFFER_PART_INFO_CMD_BASE                      = GENTL_NS(BUFFER_PART_INFO_BASE),
    KYVP_BUFFER_PART_INFO_CMD_DATA_SIZE                 = GENTL_NS(BUFFER_PART_INFO_DATA_SIZE),
    KYVP_BUFFER_PART_INFO_CMD_DATA_TYPE                 = GENTL_NS(BUFFER_PART_INFO_DATA_TYPE),
    KYVP_BUFFER_PART_INFO_CMD_DATA_FORMAT               = GENTL_NS(BUFFER_PART_INFO_DATA_FORMAT),
    KYVP_BUFFER_PART_INFO_CMD_DATA_FORMAT_NAMESPACE     = GENTL_NS(BUFFER_PART_INFO_DATA_FORMAT_NAMESPACE),
    KYVP_BUFFER_PART_INFO_CMD_WIDTH                     = GENTL_NS(BUFFER_PART_INFO_WIDTH),
    KYVP_BUFFER_PART_INFO_CMD_HEIGHT                    = GENTL_NS(BUFFER_PART_INFO_HEIGHT),
    KYVP_BUFFER_PART_INFO_CMD_XOFFSET                   = GENTL_NS(BUFFER_PART_INFO_XOFFSET),
    KYVP_BUFFER_PART_INFO_CMD_YOFFSET                   = GENTL_NS(BUFFER_PART_INFO_YOFFSET),
    KYVP_BUFFER_PART_INFO_CMD_XPADDING                  = GENTL_NS(BUFFER_PART_INFO_XPADDING),
    KYVP_BUFFER_PART_INFO_CMD_SOURCE_ID                 = GENTL_NS(BUFFER_PART_INFO_SOURCE_ID),
    KYVP_BUFFER_PART_INFO_CMD_DELIVERED_IMAGEHEIGHT     = GENTL_NS(BUFFER_PART_INFO_DELIVERED_IMAGEHEIGHT),
    KYVP_BUFFER_PART_INFO_CMD_INVALID                   = 0xFFFF
} KYVP_BUFFER_PART_INFO_CMD;

typedef enum _KYVP_PAYLOADTYPE_INFO_IDS
{
    KYVP_PAYLOADTYPE_INFO_IDS_UNKNOWN           = GENTL_NS(PAYLOAD_TYPE_UNKNOWN),
    KYVP_PAYLOADTYPE_INFO_IDS_IMAGE             = GENTL_NS(PAYLOAD_TYPE_IMAGE),
    KYVP_PAYLOADTYPE_INFO_IDS_RAW_DATA          = GENTL_NS(PAYLOAD_TYPE_RAW_DATA),
    KYVP_PAYLOADTYPE_INFO_IDS_FILE              = GENTL_NS(PAYLOAD_TYPE_FILE),
    KYVP_PAYLOADTYPE_INFO_IDS_CHUNK_DATA        = GENTL_NS(PAYLOAD_TYPE_CHUNK_DATA),
    KYVP_PAYLOADTYPE_INFO_IDS_JPEG              = GENTL_NS(PAYLOAD_TYPE_JPEG),
    KYVP_PAYLOADTYPE_INFO_IDS_JPEG2000          = GENTL_NS(PAYLOAD_TYPE_JPEG2000),
    KYVP_PAYLOADTYPE_INFO_IDS_H264              = GENTL_NS(PAYLOAD_TYPE_H264),
    KYVP_PAYLOADTYPE_INFO_IDS_CHUNK_ONLY        = GENTL_NS(PAYLOAD_TYPE_CHUNK_ONLY),
    KYVP_PAYLOADTYPE_INFO_IDS_DEVICE_SPECIFIC   = GENTL_NS(PAYLOAD_TYPE_DEVICE_SPECIFIC),
    KYVP_PAYLOADTYPE_INFO_IDS_MULTI_PART        = GENTL_NS(PAYLOAD_TYPE_MULTI_PART),
    KYVP_PAYLOADTYPE_INFO_IDS_INVALID           = 0xFFFF
} KYVP_PAYLOADTYPE_INFO_IDS;

typedef enum _KYVP_PIXELFORMAT_NAMESPACE_IDS
{
    KYVP_PIXELFORMAT_NAMESPACE_IDS_UNKNOWN      = GENTL_NS(PIXELFORMAT_NAMESPACE_UNKNOWN),
    KYVP_PIXELFORMAT_NAMESPACE_IDS_GEV          = GENTL_NS(PIXELFORMAT_NAMESPACE_GEV),
    KYVP_PIXELFORMAT_NAMESPACE_IDS_IIDC         = GENTL_NS(PIXELFORMAT_NAMESPACE_IIDC),
    KYVP_PIXELFORMAT_NAMESPACE_IDS_PFNC_16BIT   = GENTL_NS(PIXELFORMAT_NAMESPACE_PFNC_16BIT),
    KYVP_PIXELFORMAT_NAMESPACE_IDS_PFNC_32BIT   = GENTL_NS(PIXELFORMAT_NAMESPACE_PFNC_32BIT),
    KYVP_PIXELFORMAT_NAMESPACE_IDS_INVALID      = 0xFFFF
} KYVP_PIXELFORMAT_NAMESPACE_IDS;

typedef enum _KYVP_PIXELENDIANNESS_IDS
{
    KYVP_PIXELENDIANNESS_IDS_UNKNOWN    = GENTL_NS(PIXELENDIANNESS_UNKNOWN),
    KYVP_PIXELENDIANNESS_IDS_LITTLE     = GENTL_NS(PIXELENDIANNESS_LITTLE),
    KYVP_PIXELENDIANNESS_IDS_BIG        = GENTL_NS(PIXELENDIANNESS_BIG),
    KYVP_PIXELENDIANNESS_IDS_INVALID    = 0xFFFF
} KYVP_PIXELENDIANNESS_IDS;

typedef enum _KYVP_PARTDATATYPE_IDS
{
    KYVP_PARTDATATYPE_IDS_UNKNOWN               = GENTL_NS(PART_DATATYPE_UNKNOWN),
    KYVP_PARTDATATYPE_IDS_2D_IMAGE              = GENTL_NS(PART_DATATYPE_2D_IMAGE),
    KYVP_PARTDATATYPE_IDS_2D_PLANE_BIPLANAR     = GENTL_NS(PART_DATATYPE_2D_PLANE_BIPLANAR),
    KYVP_PARTDATATYPE_IDS_2D_PLANE_TRIPLANAR    = GENTL_NS(PART_DATATYPE_2D_PLANE_TRIPLANAR),
    KYVP_PARTDATATYPE_IDS_2D_PLANE_QUADPLANAR   = GENTL_NS(PART_DATATYPE_2D_PLANE_QUADPLANAR),
    KYVP_PARTDATATYPE_IDS_3D_IMAGE              = GENTL_NS(PART_DATATYPE_3D_IMAGE),
    KYVP_PARTDATATYPE_IDS_3D_PLANE_BIPLANAR     = GENTL_NS(PART_DATATYPE_3D_PLANE_BIPLANAR),
    KYVP_PARTDATATYPE_IDS_3D_PLANE_TRIPLANAR    = GENTL_NS(PART_DATATYPE_3D_PLANE_TRIPLANAR),
    KYVP_PARTDATATYPE_IDS_3D_PLANE_QUADPLANAR   = GENTL_NS(PART_DATATYPE_3D_PLANE_QUADPLANAR),
    KYVP_PARTDATATYPE_IDS_CONFIDENCE_MAP        = GENTL_NS(PART_DATATYPE_CONFIDENCE_MAP),
    KYVP_PARTDATATYPE_IDS_INVALID               = 0xFFFF
} KYVP_PARTDATATYPE_IDS;

typedef enum _KYVP_PORT_INFO_CMD
{
    KYVP_PORT_INFO_CMD_ID               = GENTL_NS(PORT_INFO_ID),
    KYVP_PORT_INFO_CMD_VENDOR           = GENTL_NS(PORT_INFO_VENDOR),
    KYVP_PORT_INFO_CMD_MODEL            = GENTL_NS(PORT_INFO_MODEL),
    KYVP_PORT_INFO_CMD_TLTYPE           = GENTL_NS(PORT_INFO_TLTYPE),
    KYVP_PORT_INFO_CMD_MODULE           = GENTL_NS(PORT_INFO_MODULE),
    KYVP_PORT_INFO_CMD_LITTLE_ENDIAN    = GENTL_NS(PORT_INFO_LITTLE_ENDIAN),
    KYVP_PORT_INFO_CMD_BIG_ENDIAN       = GENTL_NS(PORT_INFO_BIG_ENDIAN),
    KYVP_PORT_INFO_CMD_ACCESS_READ      = GENTL_NS(PORT_INFO_ACCESS_READ),
    KYVP_PORT_INFO_CMD_ACCESS_WRITE     = GENTL_NS(PORT_INFO_ACCESS_WRITE),
    KYVP_PORT_INFO_CMD_ACCESS_NA        = GENTL_NS(PORT_INFO_ACCESS_NA),
    KYVP_PORT_INFO_CMD_ACCESS_NI        = GENTL_NS(PORT_INFO_ACCESS_NI),
    KYVP_PORT_INFO_CMD_VERSION          = GENTL_NS(PORT_INFO_VERSION),
    KYVP_PORT_INFO_CMD_PORTNAME         = GENTL_NS(PORT_INFO_PORTNAME),
    KYVP_PORT_INFO_CMD_INVALID          = 0xFFFF
} KYVP_PORT_INFO_CMD;

typedef enum _KYVP_URL_SCHEME_IDS
{
    KYVP_URL_SCHEME_IDS_LOCAL           = GENTL_NS(URL_SCHEME_LOCAL),
    KYVP_URL_SCHEME_IDS_HTTP            = GENTL_NS(URL_SCHEME_HTTP),
    KYVP_URL_SCHEME_IDS_FILE            = GENTL_NS(URL_SCHEME_FILE),
    KYVP_URL_SCHEME_IDS_INVALID         = 0xFFFF
} KYVP_URL_SCHEME_IDS;

typedef enum _KYVP_URL_INFO_CMD
{
    KYVP_URL_INFO_CMD_URL                       = GENTL_NS(URL_INFO_URL),
    KYVP_URL_INFO_CMD_SCHEMA_VER_MAJOR          = GENTL_NS(URL_INFO_SCHEMA_VER_MAJOR),
    KYVP_URL_INFO_CMD_SCHEMA_VER_MINOR          = GENTL_NS(URL_INFO_SCHEMA_VER_MINOR),
    KYVP_URL_INFO_CMD_FILE_VER_MAJOR            = GENTL_NS(URL_INFO_FILE_VER_MAJOR),
    KYVP_URL_INFO_CMD_FILE_VER_MINOR            = GENTL_NS(URL_INFO_FILE_VER_MINOR),
    KYVP_URL_INFO_CMD_FILE_VER_SUBMINOR         = GENTL_NS(URL_INFO_FILE_VER_SUBMINOR),
    KYVP_URL_INFO_CMD_FILE_SHA1_HASH            = GENTL_NS(URL_INFO_FILE_SHA1_HASH),
    KYVP_URL_INFO_CMD_FILE_REGISTER_ADDRESS     = GENTL_NS(URL_INFO_FILE_REGISTER_ADDRESS),
    KYVP_URL_INFO_CMD_FILE_SIZE                 = GENTL_NS(URL_INFO_FILE_SIZE),
    KYVP_URL_INFO_CMD_SCHEME                    = GENTL_NS(URL_INFO_SCHEME),
    KYVP_URL_INFO_CMD_FILENAME                  = GENTL_NS(URL_INFO_FILENAME),
    KYVP_URL_INFO_CMD_INVALID                   = 0xFFFF
} KYVP_URL_INFO_CMD;

/**
 * @page KYVPDefines Defines
 * @section KYVP_EVENT_TYPE KYVP_EVENT_TYPE
 * | Enumerator                                             | Value  | Description                                      |
 * | ------------------------                               | ------ | ----------------                                 |
 * | KYVP_EVENT_TYPE_ERROR_                                 | 0      | Notification on module errors                    |
 * | KYVP_EVENT_TYPE_NEW_BUFFER                             | 1      | Notification on newly filled buffers |
 * | KYVP_EVENT_TYPE_FEATURE_INVALIDATE                     | 2      | Notification if a feature was changed by the Producer driver and thus needs to be invalidated |
 * | KYVP_EVENT_TYPE_FEATURE_CHANGE                         | 3      | Notification if the Producer driver wants to manually set a feature in the instance |
 * | KYVP_EVENT_TYPE_REMOTE_DEVICE                          | 4      | Notification if the Producer wants to inform the instance of the remote device that a GenApi compatible event was fired |
 * | KYVP_EVENT_TYPE_MODULE                                 | 5      | Notification that one GenTL Producer module wants to inform the GenICam GenApi instance of this module that a GenApi compatible event was fired. This Event is to be registered on any module handle except on the Remote Device |
 */
typedef enum _KYVP_EVENT_TYPE
{
    KYVP_EVENT_TYPE_ERROR_                      = GENTL_NS(EVENT_ERROR),
    KYVP_EVENT_TYPE_NEW_BUFFER                  = GENTL_NS(EVENT_NEW_BUFFER),
    KYVP_EVENT_TYPE_FEATURE_INVALIDATE          = GENTL_NS(EVENT_FEATURE_INVALIDATE),
    KYVP_EVENT_TYPE_FEATURE_CHANGE              = GENTL_NS(EVENT_FEATURE_CHANGE),
    KYVP_EVENT_TYPE_REMOTE_DEVICE               = GENTL_NS(EVENT_REMOTE_DEVICE),
    KYVP_EVENT_TYPE_MODULE                      = GENTL_NS(EVENT_MODULE),
    KYVP_EVENT_TYPE_PCIINTERFACE_EVENT          = GENTL_NS(EVENT_CUSTOM_ID + 1),
    KYVP_EVENT_TYPE_INVALID                     = 0xFFFF
} KYVP_EVENT_TYPE;

/**
 * @page KYVPDefines Defines
 * @section KYVP_EVENT_INFO_CMD KYVP_EVENT_INFO_CMD
 * | Enumerator                                             | Value  | Description                                      |
 * | ------------------------                               | ------ | ----------------                                 |
 * | KYVP_EVENT_INFO_CMD_TYPE                               | 0      | The event type of the event handle                                        |
 * | KYVP_EVENT_INFO_CMD_NUM_IN_QUEUE                       | 1      | Number of events in the event data queue                                  |
 * | KYVP_EVENT_INFO_CMD_NUM_FIRED                          | 2      | Number of events that were fired since the registration of the event      |
 * | KYVP_EVENT_INFO_CMD_EVENT_SIZE_MAX                     | 3      | Maximum size in bytes of the event data provided by the event             |
 * | KYVP_EVENT_INFO_CMD_INFO_DATA_SIZE_MAX                 | 4      | Maximum size in bytes of the information output buffer                    |
 */
typedef enum _KYVP_EVENT_INFO_CMD
{
    KYVP_EVENT_INFO_CMD_TYPE                    = GENTL_NS(EVENT_EVENT_TYPE),
    KYVP_EVENT_INFO_CMD_NUM_IN_QUEUE            = GENTL_NS(EVENT_NUM_IN_QUEUE),
    KYVP_EVENT_INFO_CMD_NUM_FIRED               = GENTL_NS(EVENT_NUM_FIRED),
    KYVP_EVENT_INFO_CMD_EVENT_SIZE_MAX          = GENTL_NS(EVENT_SIZE_MAX),
    KYVP_EVENT_INFO_CMD_INFO_DATA_SIZE_MAX      = GENTL_NS(EVENT_INFO_DATA_SIZE_MAX),
    KYVP_EVENT_INFO_CMD_INVALID                 = 0xFFFF
} KYVP_EVENT_INFO_CMD;

/**
 * @page KYVPDefines Defines
 * @section KYVP_EVENT_DATA_INFO_CMD KYVP_EVENT_DATA_INFO_CMD
 * | Enumerator                                             | Value  | Description                                      |
 * | ------------------------                               | ------ | ----------------                                 |
 * | KYVP_EVENT_DATA_INFO_CMD_ID                            | 0      | Attribute in the event data to identifythe object or feature the event refers to   |
 * | KYVP_EVENT_DATA_INFO_CMD_VALUE                         | 1      | Defines additional data to an ID                                                   |
 * | KYVP_EVENT_DATA_INFO_CMD_NUMID                         | 2      | Attribute in the event data to identify the object or feature the event refers to  |
 */
typedef enum _KYVP_EVENT_DATA_INFO_CMD
{
    KYVP_EVENT_DATA_INFO_CMD_ID         = GENTL_NS(EVENT_DATA_ID),
    KYVP_EVENT_DATA_INFO_CMD_VALUE      = GENTL_NS(EVENT_DATA_VALUE),
    KYVP_EVENT_DATA_INFO_CMD_NUMID      = GENTL_NS(EVENT_DATA_NUMID),
    KYVP_EVENT_DATA_INFO_CMD_INVALID    = 0xFFFF
} KYVP_EVENT_DATA_INFO_CMD;

/**
 * @page KYVPDefines Defines
 * @section KYVP_LINK_STATUS KYVP_LINK_STATUS
 * | Enumerator                                             | Value  | Description                                              |
 * | ------------------------                               | ------ | ----------------                                         |
 * | KYVP_KYVP_LINK_STATUS_DISCONNECTED                     | 0x00   | The link is disconnected, no link has been established   |
 * | KYVP_KYVP_LINK_STATUS_SYNCHRONIZED                     | 0x01   | The link is synchronized and awaiting connection         |
 * | KYVP_KYVP_LINK_STATUS_CONNECTING                       | 0x10   | A connection is trying to be established on link         |
 * | KYVP_KYVP_LINK_STATUS_CONNECTED                        | 0x11   | The link is connected and assigned to device             |
 */
typedef enum _KYVP_LINK_STATUS
{
    KYVP_KYVP_LINK_STATUS_DISCONNECTED  = 0x00,
    KYVP_KYVP_LINK_STATUS_SYNCHRONIZED  = 0x01,
    KYVP_KYVP_LINK_STATUS_CONNECTING    = 0x10,
    KYVP_KYVP_LINK_STATUS_CONNECTED     = 0x11
} KYVP_LINK_STATUS;

/**
 * @page KYVPDefines Defines
 * @section KYVP_LINK_STATUS_CMD KYVP_LINK_STATUS_CMD
 * | Enumerator                                             | Value  | Description                                              |
 * | ------------------------                               | ------ | ----------------                                         |
 * | KYVP_LINK_STATUS_CMD_LINK_CONNECTION_STATUS            | 0x00   | Return the link connection status                                   |
 * | KYVP_LINK_STATUS_CMD_LINK_LOCK_STATUS                  | 0x01   | Return the link lock status                               |
 */
typedef enum _KYVP_LINK_STATUS_CMD
{
    KYVP_LINK_STATUS_CMD_LINK_CONNECTION_STATUS     = 0x00,
    KYVP_LINK_STATUS_CMD_LINK_LOCK_STATUS           = 0x01,
    KYVP_LINK_STATUS_CMD_INVALID                    = 0xFF
} KYVP_LINK_STATUS_CMD;

/**
 * @page KYVPDefines Defines
 * @section KYVP_TAP_GEOMETRY KYVP_TAP_GEOMETRY
 * | Enumerator                                             | Value  | Description                                              |
 * | ------------------------                               | ------ | ----------------                                         |
 * | KYVP_STREAM_BUFFERS_TYPE_QUEUED_BUFFERS                | 0x00   | Data stream buffers type queued                          |
 * | KYVP_STREAM_BUFFERS_TYPE_CYCLIC_BUFFERS                | 0x01   | Data stream buffers type cyclic                          |
 */
typedef enum _KYVP_STREAM_BUFFERS_TYPE
{
    KYVP_STREAM_BUFFERS_TYPE_QUEUED_BUFFERS         = 0x00,
    KYVP_STREAM_BUFFERS_TYPE_CYCLIC_BUFFERS         = 0x01,
    KYVP_STREAM_BUFFERS_TYPE_INVALID                = 0xFF

}KYVP_STREAM_BUFFERS_TYPE;

/**
 * @page KYVPDefines Defines
 * @section DS_PACKED_DATA_MODE DS_PACKED_DATA_MODE
 * | Enumerator                                             | Value  | Description                                              |
 * | ------------------------                               | ------ | ----------------                                         |
 * | DS_PACKED_DATA_MODE_UNPACKED                           | 0x00   | Data unpacked                                            |
 * | DS_PACKED_DATA_MODE_LINE32ALIGNED                      | 0x01   | Data packed row aligned 32                               |
 * | DS_PACKED_DATA_MODE_LINE32ALIGNED_REVERSE              | 0x01   | Data packed row aligned 32 reverse                       |
 */
typedef enum _DS_PACKED_DATA_MODE
{
    DS_PACKED_DATA_MODE_UNPACKED                    = 0,
    DS_PACKED_DATA_MODE_LINE32ALIGNED               = 1,
    DS_PACKED_DATA_MODE_LINE32ALIGNED_REVERSE       = 2,
    DS_PACKED_DATA_MODE_INVALID                     = 0xFF
}DS_PACKED_DATA_MODE;

/**
 * @page KYVPDefines Defines
 * @section DS_PACKED_DATA_PROCESSING_MODE DS_PACKED_DATA_PROCESSING_MODE
 * | Enumerator                                             | Value  | Description                                              |
 * | ------------------------                               | ------ | ----------------                                         |
 * | DS_PACKED_DATA_PROCESSING_MODE_MSB8                    | 0x00   | Trim packed data bits to most significant 8 bits         |
 * | DS_PACKED_DATA_PROCESSING_MODE_EXPAND16                | 0x01   | Data packed bits expand to 16 bits                       |
 */
typedef enum _DS_PACKED_DATA_PROCESSING_MODE
{
    DS_PACKED_DATA_PROCESSING_MODE_MSB8             = 0,
    DS_PACKED_DATA_PROCESSING_MODE_EXPAND16         = 1,
    DS_PACKED_DATA_PROCESSING_MODE__INVALID         = 0xFF
}DS_PACKED_DATA_PROCESSING_MODE;

/////////////////////////////////////////////////////////////////////////////////////////////////////
/// KYVP HANDLES
/////////////////////////////////////////////////////////////////////////////////////////////////////

#define INVALID_HANDLE_INIT_VALUE ((void*)-1)

/**
 * @page KYVPLibTL_Handles KYVPLibTL Handles
 * @section KYVPLibTL_HandlesTable Library handles
 * | Handle                             |Name                       | Description                                   |
 * | ------                             | --------                  | -------                                       |
 * | @ref KYVP_TL_HANDLE                | Library handle            | Handle for System Module access               |
 * | @ref KYVP_PCI_INTERFACE_HANDLE     | PCI Interface handle      | Handle for PCI Interface Module access        |
 * | @ref KYVP_DEVICE_HANDLE            | Local device handle       | Handle for Local Device Module access         |
 * | @ref KYVP_REMOTE_DEVICE_HANDLE     | Remote device handle      | Handle for Remote Device Module access        |
 * | @ref KYVP_STREAM_HANDLE            | Stream interface handle   | Handle for Data Stream Module access          |
 * | @ref KYVP_BUFFER_HANDLE            | Buffer interface handle   | Handle for Buffer Module access               |
 */
/**
 * @page KYVPLibTL_Handles KYVPLibTL Handles
 * @section KYVP_TL_HANDLE KYVP_TL_HANDLE
 * | Structure Field    | Description                       | Remarks   |
 * | -------------      | -----------                       | ----      |
 * | pNativePointer     | Native pointer to handler         |           |
*/
/**
 * @page KYVPLibTL_Handles KYVPLibTL Handles
 * @section KYVP_PCI_INTERFACE_HANDLE KYVP_PCI_INTERFACE_HANDLE
 * | Structure Field    | Description                       | Remarks   |
 * | -------------      | -----------                       | ----      |
 * | pNativePointer     | Native pointer to handler         |           |
 */
/**
 * @page KYVPLibTL_Handles KYVPLibTL Handles
 * @section KYVP_DEVICE_HANDLE KYVP_DEVICE_HANDLE
 * | Structure Field    | Description                       | Remarks   |
 * | -------------      | -----------                       | ----      |
 * | pNativePointer     | Native pointer to handler         |           |
*/
/**
 * @page KYVPLibTL_Handles KYVPLibTL Handles
 * @section KYVP_REMOTE_DEVICE_HANDLE KYVP_REMOTE_DEVICE_HANDLE
 * | Structure Field    | Description                       | Remarks   |
 * | -------------      | -----------                       | ----      |
 * | pNativePointer     | Native pointer to handler         |           |
*/
/**
 * @page KYVPLibTL_Handles KYVPLibTL Handles
 * @section KYVP_STREAM_HANDLE KYVP_STREAM_HANDLE
 * | Structure Field    | Description                       | Remarks   |
 * | -------------      | -----------                       | ----      |
 * | pNativePointer     | Native pointer to handler         |           |
*/
/**
 * @page KYVPLibTL_Handles KYVPLibTL Handles
 * @section KYVP_BUFFER_HANDLE KYVP_BUFFER_HANDLE
 * | Structure Field    | Description                       | Remarks   |
 * | -------------      | -----------                       | ----      |
 * | pNativePointer     | Native pointer to handler         |           |
*/
//////////////////////////////////////////////////////////////////////
/// KYVP_TL_HANDLE DEFINES
//////////////////////////////////////////////////////////////////////

#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVP_TL_HANDLE
{
#ifdef __cplusplus
    _KYVP_TL_HANDLE()
        : pNativePointer(INVALID_HANDLE_INIT_VALUE)
    {
    }

    constexpr
        void* operator()() const { return pNativePointer; } // DO NOT USE!!! Will be removed in future versions

protected:
    _KYVP_TL_HANDLE(void* _pNativePointer)
        : pNativePointer(_pNativePointer)
    {
    }
#endif
    void* pNativePointer;
} KYVP_TL_HANDLE;
#pragma pack(pop)/** @endcond */

KYVPTL_VAR const KYVP_TL_HANDLE KYVP_TL_HANDLE_NULL;
KYVPTL_VAR const KYVP_TL_HANDLE KYVP_TL_HANDLE_INVALID;

#define TL_HANDLE_IS_INVALID(_hHandle)                      KYVPLibTL_TLHandleIsInvalid(_hHandle)
#define TL_HANDLE_IS_NULL(_hHandle)                         KYVPLibTL_TLHandleIsNull(_hHandle)
#define TL_HANDLES_IS_EQUAL(_hHandleFirst, _hHandleSecond)  KYVPLibTL_TLHandlesIsEqual(_hHandleFirst, _hHandleSecond)

//////////////////////////////////////////////////////////////////////
/// KYVP_PCI_INTERFACE_HANDLE DEFINES
//////////////////////////////////////////////////////////////////////

#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVP_PCI_INTERFACE_HANDLE
{
#ifdef __cplusplus
    _KYVP_PCI_INTERFACE_HANDLE()
        : pNativePointer(INVALID_HANDLE_INIT_VALUE)
    {
    }

    constexpr
        void* operator()() const { return pNativePointer; } // DO NOT USE!!! Will be removed in future versions

protected:
    _KYVP_PCI_INTERFACE_HANDLE(void* _pNativePointer)
        : pNativePointer(_pNativePointer)
    {
    }


#endif
    void* pNativePointer;

} KYVP_PCI_INTERFACE_HANDLE;
#pragma pack(pop)/** @endcond */

KYVPTL_VAR const KYVP_PCI_INTERFACE_HANDLE KYVP_PCI_INTERFACE_HANDLE_NULL;
KYVPTL_VAR const KYVP_PCI_INTERFACE_HANDLE KYVP_PCI_INTERFACE_HANDLE_INVALID;

#define PCI_INTERFACE_HANDLE_IS_INVALID(_hHandle)                       KYVPLibTL_IFHandleIsInvalid(_hHandle)
#define PCI_INTERFACE_HANDLE_IS_NULL(_hHandle)                          KYVPLibTL_IFHandleIsNull(_hHandle)
#define PCI_INTERFACE_HANDLES_IS_EQUAL(_hHandleFirst, _hHandleSecond)   KYVPLibTL_IFHandlesAreEqual(_hHandleFirst, _hHandleSecond)

//////////////////////////////////////////////////////////////////////
/// KYVP_DEVICE_HANDLE DEFINES
//////////////////////////////////////////////////////////////////////

#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVP_DEVICE_HANDLE
{
#ifdef __cplusplus
    _KYVP_DEVICE_HANDLE()
        : pNativePointer(INVALID_HANDLE_INIT_VALUE)
    {
    }

    constexpr
        void* operator()() const { return pNativePointer; } // DO NOT USE!!! Will be removed in future versions

protected:
    _KYVP_DEVICE_HANDLE(void* _pNativePointer)
        : pNativePointer(_pNativePointer)
    {
    }
#endif
    void* pNativePointer;

} KYVP_DEVICE_HANDLE;
#pragma pack(pop)/** @endcond */

KYVPTL_VAR const KYVP_DEVICE_HANDLE KYVP_DEVICE_HANDLE_NULL;
KYVPTL_VAR const KYVP_DEVICE_HANDLE KYVP_DEVICE_HANDLE_INVALID;

#define DEVICE_HANDLE_IS_INVALID(_hHandle)                      KYVPLibTL_DevHandleIsInvalid(_hHandle)
#define DEVICE_HANDLE_IS_NULL(_hHandle)                         KYVPLibTL_DevHandleIsNull(_hHandle)
#define DEVICE_HANDLES_IS_EQUAL(_hHandleFirst, _hHandleSecond)  KYVPLibTL_DevHandlesIsEqual(_hHandleFirst, _hHandleSecond)

//////////////////////////////////////////////////////////////////////
/// KYVP_REMOTE_DEVICE_HANDLE DEFINES
//////////////////////////////////////////////////////////////////////

#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVP_REMOTE_DEVICE_HANDLE
{
#ifdef __cplusplus
    _KYVP_REMOTE_DEVICE_HANDLE()
        : pNativePointer(INVALID_HANDLE_INIT_VALUE)
    {
    }

    constexpr
        void* operator()() const { return pNativePointer; } // DO NOT USE!!! Will be removed in future versions

protected:
    _KYVP_REMOTE_DEVICE_HANDLE(void* _pNativePointer)
        : pNativePointer(_pNativePointer)
    {
    }
#endif
    void* pNativePointer;

} KYVP_REMOTE_DEVICE_HANDLE;
#pragma pack(pop)/** @endcond */

KYVPTL_VAR const KYVP_REMOTE_DEVICE_HANDLE KYVP_REMOTE_DEVICE_HANDLE_NULL;
KYVPTL_VAR const KYVP_REMOTE_DEVICE_HANDLE KYVP_REMOTE_DEVICE_HANDLE_INVALID;

#define REMOTE_DEVICE_HANDLE_IS_INVALID(_hHandle)                       KYVPLibTL_RemoteDeviceHandleIsInvalid(_hHandle)
#define REMOTE_DEVICE_HANDLE_IS_NULL(_hHandle)                          KYVPLibTL_RemoteDeviceHandleIsNull(_hHandle)
#define REMOTE_DEVICE_HANDLES_IS_EQUAL(_hHandleFirst, _hHandleSecond)   KYVPLibTL_RemoteDeviceHandlesIsEqual(_hHandleFirst, _hHandleSecond)

//////////////////////////////////////////////////////////////////////
/// KYVP_STREAM_HANDLE DEFINES
//////////////////////////////////////////////////////////////////////

#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVP_STREAM_HANDLE
{
#ifdef __cplusplus
    _KYVP_STREAM_HANDLE()
        : pNativePointer(INVALID_HANDLE_INIT_VALUE)
    {
    }

    constexpr
        void* operator()() const { return pNativePointer; } // DO NOT USE!!! Will be removed in future versions

protected:
    _KYVP_STREAM_HANDLE(void* _pNativePointer)
        : pNativePointer(_pNativePointer)
    {
    }
#endif
    void* pNativePointer;

} KYVP_STREAM_HANDLE;
#pragma pack(pop)/** @endcond */

KYVPTL_VAR const KYVP_STREAM_HANDLE KYVP_STREAM_HANDLE_NULL;
KYVPTL_VAR const KYVP_STREAM_HANDLE KYVP_STREAM_HANDLE_INVALID;

#define STREAM_HANDLE_IS_INVALID(_hHandle)                          KYVPLibTL_DSHandleIsInvalid(_hHandle)
#define STREAM_HANDLE_IS_NULL(_hHandle)                             KYVPLibTL_DSHandleIsNull(_hHandle)
#define STREAM_HANDLES_IS_EQUAL(_hHandleFirst, _hHandleSecond)      KYVPLibTL_DSHandlesIsEqual(_hHandleFirst, _hHandleSecond)

//////////////////////////////////////////////////////////////////////
/// KYVP_BUFFER_HANDLE DEFINES
//////////////////////////////////////////////////////////////////////

#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVP_BUFFER_HANDLE
{
#ifdef __cplusplus
    _KYVP_BUFFER_HANDLE()
        : pNativePointer(INVALID_HANDLE_INIT_VALUE)
    {
    }

    constexpr
        void* operator()() const { return pNativePointer; } // DO NOT USE!!! Will be removed in future versions

protected:
    _KYVP_BUFFER_HANDLE(void* _pNativePointer)
        : pNativePointer(_pNativePointer)
    {
    }
#endif
    void* pNativePointer;

} KYVP_BUFFER_HANDLE;
#pragma pack(pop)/** @endcond */

KYVPTL_VAR const KYVP_BUFFER_HANDLE KYVP_BUFFER_HANDLE_NULL;
KYVPTL_VAR const KYVP_BUFFER_HANDLE KYVP_BUFFER_HANDLE_INVALID;

#define BUFFER_HANDLE_IS_INVALID(_hHandle)                          KYVPLibTL_BufferHandleIsInvalid(_hHandle)
#define BUFFER_HANDLE_IS_NULL(_hHandle)                             KYVPLibTL_BufferHandleIsNull(_hHandle)
#define BUFFER_HANDLES_IS_EQUAL(_hHandleFirst, _hHandleSecond)      KYVPLibTL_BufferHandlesIsEqual(_hHandleFirst, _hHandleSecond)

//////////////////////////////////////////////////////////////////////
/// KYVP_EVENT_HANDLE DEFINES
//////////////////////////////////////////////////////////////////////

#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVP_EVENT_HANDLE
{
#ifdef __cplusplus
    _KYVP_EVENT_HANDLE()
        : pNativePointer(INVALID_HANDLE_INIT_VALUE)
    {
    }

    constexpr
        void* operator()() const { return pNativePointer; } // DO NOT USE!!! Will be removed in future versions

protected:
    _KYVP_EVENT_HANDLE(void* _pNativePointer)
        : pNativePointer(_pNativePointer)
    {
    }
#endif
    void* pNativePointer;
} KYVP_EVENT_HANDLE;
#pragma pack(pop)/** @endcond */

#if defined(_MSC_VER)
#  pragma warning(pop)
#endif

KYVPTL_VAR const KYVP_EVENT_HANDLE KYVP_EVENT_HANDLE_NULL;
KYVPTL_VAR const KYVP_EVENT_HANDLE KYVP_EVENT_HANDLE_INVALID;

#define EVENT_HANDLE_IS_INVALID(_hHandle)                           KYVPLibTL_EventHandleIsInvalid(_hHandle)
#define EVENT_HANDLE_IS_NULL(_hHandle)                              KYVPLibTL_EventHandleIsNull(_hHandle)
#define EVENT_HANDLES_IS_EQUAL(_hHandleFirst, _hHandleSecond)       KYVPLibTL_EventHandlesIsEqual(_hHandleFirst, _hHandleSecond)

//////////////////////////////////////////////////////////////////////
/// NEW BUFFER KYVPLIBTL EVENT STRUCTURE
//////////////////////////////////////////////////////////////////////

/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLIBTL_EVENT_NEW_BUFFER_DATA KYVPLIBTL_EVENT_NEW_BUFFER_DATA
 * | Structure Field    | Type                          | Description                                           | Remarks   |
 * | -------------      | -----                         | -----------                                           | ----      |
 * | hBufferHandle      | @ref KYVP_BUFFER_HANDLE       | Buffer handle which contains new data                 |           |
 * | pUserPointer       | void*                         | User pointer provided at announcement of the buffer.  |           |
 */
#   pragma pack (push, 1)
typedef struct _KYVPLIBTL_EVENT_NEW_BUFFER_DATA
{
    KYVP_BUFFER_HANDLE                  hBufferHandle   KYVP_MEMBER_DEFAULT(KYVP_BUFFER_HANDLE_INVALID); /* Buffer handle which contains new data. */
    void*                               pUserPointer    KYVP_MEMBER_DEFAULT(nullptr); /* User pointer provided at announcement of the buffer. */
} KYVPLIBTL_EVENT_NEW_BUFFER_DATA;
#   pragma pack (pop)

////////////////////////////////////////////////////////////////////////////////////
/// PCIINTERFACE EVENTS DEFINES
////////////////////////////////////////////////////////////////////////////////////

#pragma pack(push, 1)

#ifdef __cplusplus
static const int KYVP_PCIINTERFACE_EVENT_TEMPERATURE_VERSION = 1;
#else
#define KYVP_PCIINTERFACE_EVENT_TEMPERATURE_VERSION 1
#endif
typedef struct _KYVP_PCIINTERFACE_EVENT_TEMPERATURE
{
    uint32_t                                            version     KYVP_MEMBER_DEFAULT(KYVP_PCIINTERFACE_EVENT_TEMPERATURE_VERSION);
    KYVP_PCIINTERFACE_EVENT_TEMPERATURE_THRESHOLD_ID    eTemperatureThresholdId;

}KYVP_PCIINTERFACE_EVENT_TEMPERATURE;

#ifdef __cplusplus
static const int KYVP_PCIINTERFACE_EVENT_CXP2_HEARTBEAT_VERSION = 1;
#else
#define KYVP_PCIINTERFACE_EVENT_CXP2_HEARTBEAT_VERSION 1
#endif
typedef struct _KYVP_PCIINTERFACE_EVENT_CXP2_HEARTBEAT
{
    uint32_t                                            version     KYVP_MEMBER_DEFAULT(KYVP_PCIINTERFACE_EVENT_CXP2_HEARTBEAT_VERSION);
    KYVP_DEVICE_HANDLE                                  deviceHandle;
    KYVP_CXP2_HEARTBEAT                                 heartBeat;

}KYVP_PCIINTERFACE_EVENT_CXP2_HEARTBEAT;

#ifdef __cplusplus
static const int KYVP_PCIINTERFACE_EVENT_CXP2_EVENT_VERSION = 1;
#else
#define KYVP_PCIINTERFACE_EVENT_CXP2_EVENT_VERSION 1
#endif
typedef struct _KYVP_PCIINTERFACE_EVENT_CXP2_EVENT
{
    uint32_t                                            version     KYVP_MEMBER_DEFAULT(KYVP_PCIINTERFACE_EVENT_CXP2_EVENT_VERSION);
    KYVP_DEVICE_HANDLE                                  deviceHandle;
    KYVP_CXP2_EVENT                                     cxp2Event;

}KYVP_PCIINTERFACE_EVENT_CXP2_EVENT;

#ifdef __cplusplus
static const int KYVP_PCIINTERFACE_GENERIC_EVENT_VERSION = 1;
#else
#define KYVP_PCIINTERFACE_GENERIC_EVENT_VERSION 1
#endif
typedef struct _KYVP_PCIINTERFACE_GENERIC_EVENT
{
    uint32_t                                            version     KYVP_MEMBER_DEFAULT(KYVP_PCIINTERFACE_GENERIC_EVENT_VERSION);
    KYVP_PCIINTERFACE_EVENT_ID                          eEventId;

    union
    {
        KYVP_PCIINTERFACE_EVENT_TEMPERATURE             eventTemperature;
        KYVP_PCIINTERFACE_EVENT_CXP2_HEARTBEAT          eventCXP2Heartbeat;
        KYVP_PCIINTERFACE_EVENT_CXP2_EVENT              eventCXP2Event;
    } data;

}KYVP_PCIINTERFACE_GENERIC_EVENT;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////
/// ARGUMENT STRUCTURES
//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_GETPORTURL_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_GETPORTURL_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_GetPortUrl_Args KYVPLibTL_GetPortUrl_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hTLHandle          | @ref KYVP_TL_HANDLE               | TL Handle                                             |           |
 * | pszURL             | char*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_GetPortUrl_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_GETPORTURL_STRUCT_VERSION);
    KYVP_TL_HANDLE                          hTLHandle   KYVP_MEMBER_DEFAULT(KYVP_TL_HANDLE_INVALID);
    char*                                   pszURL      KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

}KYVPLibTL_GetPortUrl_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_READPORT_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_READPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_ReadPort_Args KYVPLibTL_ReadPort_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hTLHandle          | @ref KYVP_TL_HANDLE               | TL Handle                                             |           |
 * | iAddress           | uint64_t                          | Port address                                          |           |
 * | pBuffer            | void*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_ReadPort_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_READPORT_STRUCT_VERSION);
    KYVP_TL_HANDLE                          hTLHandle   KYVP_MEMBER_DEFAULT(KYVP_TL_HANDLE_INVALID);
    uint64_t                                iAddress    KYVP_MEMBER_DEFAULT(0);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);
}KYVPLibTL_ReadPort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_WRITEPORT_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_WRITEPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_WritePort_Args KYVPLibTL_WritePort_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hTLHandle          | @ref KYVP_TL_HANDLE               | TL Handle                                             |           |
 * | iAddress           | uint64_t                          | Port address                                          |           |
 * | pBuffer            | void*                             | Pointer to user allocated const buffer                |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_WritePort_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_WRITEPORT_STRUCT_VERSION);
    KYVP_TL_HANDLE                          hTLHandle   KYVP_MEMBER_DEFAULT(KYVP_TL_HANDLE_INVALID);
    uint64_t                                iAddress    KYVP_MEMBER_DEFAULT(0);
    const void*                             pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t* piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_WritePort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_PCIINTERFACEGETPORTURL_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_PCIINTERFACEGETPORTURL_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_PCIInterface_GetPortUrl_Args KYVPLibTL_PCIInterface_GetPortUrl_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hIFHandle          | @ref KYVP_PCI_INTERFACE_HANDLE    | Interface Handle                                      |           |
 * | pszURL             | char*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_PCIInterface_GetPortUrl_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_PCIINTERFACEGETPORTURL_STRUCT_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    char*                                   pszURL      KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

}KYVPLibTL_PCIInterface_GetPortUrl_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_PCIINTERFACEREADPORT_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_PCIINTERFACEREADPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_PCIInterface_ReadPort_Args KYVPLibTL_PCIInterface_ReadPort_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hIFHandle          | @ref KYVP_PCI_INTERFACE_HANDLE    | Interface Handle                                      |           |
 * | iAddress           | uint64_t                          | Port address                                          |           |
 * | pBuffer            | void*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_PCIInterface_ReadPort_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_PCIINTERFACEREADPORT_STRUCT_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                                iAddress    KYVP_MEMBER_DEFAULT(0);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);
}KYVPLibTL_PCIInterface_ReadPort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_PCIINTERFACEWRITEPORT_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_PCIINTERFACEWRITEPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_PCIInterface_WritePort_Args KYVPLibTL_PCIInterface_WritePort_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hIFHandle          | @ref KYVP_PCI_INTERFACE_HANDLE    | Interface Handle                                      |           |
 * | iAddress           | uint64_t                          | Port address                                          |           |
 * | pBuffer            | void*                             | Pointer to user allocated const buffer                |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_PCIInterface_WritePort_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_PCIINTERFACEWRITEPORT_STRUCT_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint64_t                                iAddress    KYVP_MEMBER_DEFAULT(0);
    const void*                             pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_PCIInterface_WritePort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_DEVICEGETPORTURL_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_DEVICEGETPORTURL_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_Device_GetPortUrl_Args KYVPLibTL_Device_GetPortUrl_Args
 * | Structure Field    | Type                          | Description                                           | Remarks   |
 * | -------------      | -----                         | -----------                                           | ----      |
 * | version            | uint32_t                      | struct version                                        |           |
 * | hDevHandle         | @ref KYVP_DEVICE_HANDLE       | Local device handle                                   |           |
 * | pszURL             | char*                         | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                       | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_Device_GetPortUrl_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_DEVICEGETPORTURL_STRUCT_VERSION);
    KYVP_DEVICE_HANDLE                      hDevHandle  KYVP_MEMBER_DEFAULT(KYVP_DEVICE_HANDLE_INVALID);
    char*                                   pszURL      KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_Device_GetPortUrl_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int  KYVPLIBTL_DEVICEREADPORT_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_DEVICEREADPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_Device_ReadPort_Args KYVPLibTL_Device_ReadPort_Args
 * | Structure Field    | Type                          | Description                                           | Remarks   |
 * | -------------      | -----                         | -----------                                           | ----      |
 * | version            | uint32_t                      | struct version                                        |           |
 * | hDevHandle         | @ref KYVP_DEVICE_HANDLE       | Local device handle                                   |           |
 * | iAddress           | uint64_t                      | Port address                                          |           |
 * | pBuffer            | void*                         | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                       | Pointer to user allocated size_t buffer               |           |
*/
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_Device_ReadPort_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_DEVICEREADPORT_STRUCT_VERSION);
    KYVP_DEVICE_HANDLE                      hDevHandle  KYVP_MEMBER_DEFAULT(KYVP_DEVICE_HANDLE_INVALID);
    uint64_t                                iAddress    KYVP_MEMBER_DEFAULT(0);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_Device_ReadPort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_DEVICEWRITEPORT_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_DEVICEWRITEPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_Device_WritePort_Args KYVPLibTL_Device_WritePort_Args
 * | Structure Field    | Type                          | Description                                           | Remarks   |
 * | -------------      | -----                         | -----------                                           | ----      |
 * | version            | uint32_t                      | struct version                                        |           |
 * | hDevHandle         | @ref KYVP_DEVICE_HANDLE       | Local device handle                                   |           |
 * | iAddress           | uint64_t                      | Port address                                          |           |
 * | pBuffer            | const void*                   | Pointer to user allocated const buffer                |           |
 * | piSize             | size_t*                       | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_Device_WritePort_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_DEVICEWRITEPORT_STRUCT_VERSION);
    KYVP_DEVICE_HANDLE                      hDevHandle  KYVP_MEMBER_DEFAULT(KYVP_DEVICE_HANDLE_INVALID);
    uint64_t                                iAddress    KYVP_MEMBER_DEFAULT(0);
    const void*                             pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_Device_WritePort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_REMOTEDEVICEGETPORTURL_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_REMOTEDEVICEGETPORTURL_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_RemoteDevice_GetPortUrl_Args KYVPLibTL_RemoteDevice_GetPortUrl_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hDevHandle         | @ref KYVP_REMOTE_DEVICE_HANDLE    | Remote device handle                                  |           |
 * | pszURL             | char*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_RemoteDevice_GetPortUrl_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPLIBTL_REMOTEDEVICEGETPORTURL_STRUCT_VERSION);
    KYVP_REMOTE_DEVICE_HANDLE               hRemoteDeviceHandle     KYVP_MEMBER_DEFAULT(KYVP_REMOTE_DEVICE_HANDLE_INVALID);
    char*                                   pszURL                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize                  KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_RemoteDevice_GetPortUrl_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_REMOTEDEVICEREADPORT_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_REMOTEDEVICEREADPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_RemoteDevice_ReadPort_Args KYVPLibTL_RemoteDevice_ReadPort_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hDevHandle         | @ref KYVP_REMOTE_DEVICE_HANDLE    | Remote device handle                                  |           |
 * | pBuffer            | void*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_RemoteDevice_ReadPort_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPLIBTL_REMOTEDEVICEREADPORT_STRUCT_VERSION);
    KYVP_REMOTE_DEVICE_HANDLE               hRemoteDeviceHandle     KYVP_MEMBER_DEFAULT(KYVP_REMOTE_DEVICE_HANDLE_INVALID);
    uint64_t                                iAddress                KYVP_MEMBER_DEFAULT(0);
    void*                                   pBuffer                 KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize                  KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_RemoteDevice_ReadPort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_REMOTEDEVICEWRITEPORT_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_REMOTEDEVICEWRITEPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_RemoteDevice_WritePort_Args KYVPLibTL_RemoteDevice_WritePort_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hDevHandle         | @ref KYVP_REMOTE_DEVICE_HANDLE    | Remote device handle                                  |           |
 * | iAddress           | uint64_t                          | Port Address                                          |           |
 * | pBuffer            | const void*                       | Pointer to user allocated const buffer                |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_RemoteDevice_WritePort_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPLIBTL_REMOTEDEVICEWRITEPORT_STRUCT_VERSION);
    KYVP_REMOTE_DEVICE_HANDLE               hRemoteDeviceHandle     KYVP_MEMBER_DEFAULT(KYVP_REMOTE_DEVICE_HANDLE_INVALID);
    uint64_t                                iAddress                KYVP_MEMBER_DEFAULT(0);
    const void*                             pBuffer                 KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize                  KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_RemoteDevice_WritePort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_STREAMGETPORTURL_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_STREAMGETPORTURL_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_Stream_GetPortUrl_Args KYVPLibTL_Stream_GetPortUrl_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hStreamHandle      | @ref KYVP_STREAM_HANDLE           | Stream handle                                         |           |
 * | pszURL             | char*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_Stream_GetPortUrl_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPLIBTL_STREAMGETPORTURL_STRUCT_VERSION);
    KYVP_STREAM_HANDLE                      hStreamHandle           KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    char*                                   pszURL                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize                  KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_Stream_GetPortUrl_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_STREAMREADPORT_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_STREAMREADPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_Stream_ReadPort_Args KYVPLibTL_Stream_ReadPort_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hStreamHandle      | @ref KYVP_STREAM_HANDLE           | Stream handle                                         |           |
 * | iAddress           | uint64_t                          | Port address                                          |           |
 * | pBuffer            | void*                             | Pointer to user allocated buffer                      |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_Stream_ReadPort_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPLIBTL_STREAMREADPORT_STRUCT_VERSION);
    KYVP_STREAM_HANDLE                      hStreamHandle           KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    uint64_t                                iAddress                KYVP_MEMBER_DEFAULT(0);
    void*                                   pBuffer                 KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize                  KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_Stream_ReadPort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_STREAMWRITEPORT_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_STREAMWRITEPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_Stream_WritePort_Args KYVPLibTL_Stream_WritePort_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hStreamHandle      | @ref KYVP_STREAM_HANDLE           | Stream handle                                         |           |
 * | iAddress           | uint64_t                          | Port address                                          |           |
 * | pBuffer            | const void*                       | Pointer to user allocated const buffer                |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t buffer               |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_Stream_WritePort_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPLIBTL_STREAMWRITEPORT_STRUCT_VERSION);
    KYVP_STREAM_HANDLE                      hStreamHandle           KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    uint64_t                                iAddress                KYVP_MEMBER_DEFAULT(0);
    const void*                             pBuffer                 KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize                  KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_Stream_WritePort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_TLOpen_Args_VERSION = 1;
#else
#define KYVPLibTL_TLOpen_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_TLOpen_Args KYVPLibTL_TLOpen_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | phTLHandle         | @ref KYVP_TL_HANDLE*              | Pointer to @ref KYVP_TL_HANDLE                        |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_TLOpen_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_TLOpen_Args_VERSION);
    KYVP_TL_HANDLE*                         phTLHandle  KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_TLOpen_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_TLClose_Args_VERSION = 1;
#else
#define KYVPLibTL_TLClose_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_TLClose_Args KYVPLibTL_TLClose_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | phTLHandle         | @ref KYVP_TL_HANDLE               | KYVP_TL HANDLE                                        |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_TLClose_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_TLClose_Args_VERSION);
    KYVP_TL_HANDLE                          hTLHandle   KYVP_MEMBER_DEFAULT(KYVP_TL_HANDLE_INVALID);

} KYVPLibTL_TLClose_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_TLGetInfo_Args_VERSION = 1;
#else
#define KYVPLibTL_TLGetInfo_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_TLGetInfo_Args KYVPLibTL_TLGetInfo_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | phTLHandle         | @ref KYVP_TL_HANDLE               | KYVP_TL HANDLE                                        |           |
 * | eInfoCmd           | @ref KYVP_TL_INFO_CMD             | Information to be retrieved as defined in @ref KYVP_TL_INFO_CMD |           |
 * | pType              | @ref KYVP_INFO_DATATYPE           | Data type of the pBuffer content as defined in the @ref KYVP_INFO_DATATYPE |
 * | pBuffer            | void*                             | Pointer to a user allocated buffer to receive the requested information. If this parameter is NULL, piSize will contain the minimal size of pBuffer in bytes. If the iType is a string the size includes the terminating 0.|
 * | piSize             | size_t*                           | Number of bytes filled by the function                |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_TLGetInfo_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_TLGetInfo_Args_VERSION);
    KYVP_TL_HANDLE                          hTLHandle   KYVP_MEMBER_DEFAULT(KYVP_TL_HANDLE_INVALID);
    KYVP_TL_INFO_CMD                        eInfoCmd    KYVP_MEMBER_DEFAULT(KYVP_TL_INFO_CMD_ID);
    KYVP_INFO_DATATYPE*                     pType       KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_TLGetInfo_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_TLGetNumInterfaces_Args_VERSION = 1;
#else
#define KYVPLibTL_TLGetNumInterfaces_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_TLGetNumInterfaces_Args KYVPLibTL_TLGetNumInterfaces_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hTLHandle          | @ref KYVP_TL_HANDLE               | @ref KYVP_TL HANDLE                                   |           |
 * | piNumIfaces        | uint32_t*                         | Pointer to user allocated uint32_t                    |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_TLGetNumInterfaces_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_TLGetNumInterfaces_Args_VERSION);
    KYVP_TL_HANDLE                          hTLHandle   KYVP_MEMBER_DEFAULT(KYVP_TL_HANDLE_INVALID);
    uint32_t*                               piNumIfaces KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_TLGetNumInterfaces_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_TLGetInterfaceID_Args_VERSION = 1;
#else
#define KYVPLibTL_TLGetInterfaceID_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_TLGetInterfaceID_Args KYVPLibTL_TLGetInterfaceID_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hTLHandle          | @ref KYVP_TL_HANDLE               | @ref KYVP_TL HANDLE                                   |           |
 * | iIndex             | uint32_t                          | Interface index                                       |           |
 * | pszID              | char*                             | Pointer to user allocated char* buffer                |           |
 * | piSize             | size_t*                           | Pointer to user allocated size_t* buffer              |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_TLGetInterfaceID_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_TLGetInterfaceID_Args_VERSION);
    KYVP_TL_HANDLE                          hTLHandle   KYVP_MEMBER_DEFAULT(KYVP_TL_HANDLE_INVALID);
    uint32_t                                iIndex      KYVP_MEMBER_DEFAULT(0);
    char*                                   pszID       KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_TLGetInterfaceID_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_TLGetPCIInterfaceInfo_Args_VERSION = 1;
#else
#define KYVPLibTL_TLGetPCIInterfaceInfo_Args_VERSION 1
#endif

/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_TLGetPCIInterfaceInfo_Args KYVPLibTL_TLGetPCIInterfaceInfo_Args
 * | Structure Field    | Type                              | Description                                           | Remarks   |
 * | -------------      | -----                             | -----------                                           | ----      |
 * | version            | uint32_t                          | struct version                                        |           |
 * | hTLHandle          | @ref KYVP_TL_HANDLE               | @ref KYVP_TL HANDLE                                   |           |
 * | pszIfaceID         | const char*                       | Interface ID from KYVPLibTL_TLGetInterfaceID func     |           |
 * | eInfoCmd           | KYVP_INTERFACE_INFO_CMD           | Information to be retrieved                           |           |
 * | pType              | KYVP_INFO_DATATYPE*               | Data type of the pBuffer content                      |           |
 * | pBuffer            | void*                             | Pointer to a user allocated buffer                    |           |
 * | piSize             | size_t*                           | out: minimal size of pBuffer                          |           |        
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_TLGetPCIInterfaceInfo_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_TLGetPCIInterfaceInfo_Args_VERSION);
    KYVP_TL_HANDLE                          hTLHandle   KYVP_MEMBER_DEFAULT(KYVP_TL_HANDLE_INVALID);
    const char*                             pszIfaceID  KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_INTERFACE_INFO_CMD                 eInfoCmd    KYVP_MEMBER_DEFAULT(KYVP_INTERFACE_INFO_CMD_ID);
    KYVP_INFO_DATATYPE*                     pType       KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_TLGetPCIInterfaceInfo_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_TLOPENPCIINTERFACE_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_TLOPENPCIINTERFACE_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_TLOpenInterface_Args KYVPLibTL_TLOpenInterface_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hTLHandle              | @ref KYVP_TL_HANDLE               | @ref KYVP_TL HANDLE                                       |           |
 * | pszIfaceID             | const char*                       | Unique interface ID to open as a 0-terminated C string    |           |
 * | phPCIInterfaceHandle   | @ref KYVP_PCI_INTERFACE_HANDLE*   | Pointer to @ref KYVP_PCI_INTERFACE_HANDLE                 |           |    
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_TLOpenInterface_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPLIBTL_TLOPENPCIINTERFACE_STRUCT_VERSION);
    KYVP_TL_HANDLE                          hTLHandle               KYVP_MEMBER_DEFAULT(KYVP_TL_HANDLE_INVALID);
    const char*                             pszIfaceID              KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_PCI_INTERFACE_HANDLE*              phPCIInterfaceHandle    KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_TLOpenInterface_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_TLUpdatePCIInterfaceList_Args_VERSION = 1;
#else
#define KYVPLibTL_TLUpdatePCIInterfaceList_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_TLUpdatePCIInterfaceList_Args KYVPLibTL_TLUpdatePCIInterfaceList_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hTLHandle              | @ref KYVP_TL_HANDLE               | @ref KYVP_TL HANDLE                                       |           |
 * | pbChanged              | KY_BOOL*                          | Contains KY_TRUE if the internal list was changed and KY_FALSE otherwise. If set to NULL nothing is written to this parameter | |
 * | iTimeout               | uint64_t                          | Timeout in ms.                                            |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_TLUpdatePCIInterfaceList_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_TLUpdatePCIInterfaceList_Args_VERSION);
    KYVP_TL_HANDLE                          hTLHandle   KYVP_MEMBER_DEFAULT(KYVP_TL_HANDLE_INVALID);
    KY_BOOL*                                pbChanged   KYVP_MEMBER_DEFAULT(nullptr);
    uint64_t                                iTimeout    KYVP_MEMBER_DEFAULT(0);

} KYVPLibTL_TLUpdatePCIInterfaceList_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_IFClose_Args_VERSION = 1;
#else
#define KYVPLibTL_IFClose_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_IFClose_Args KYVPLibTL_IFClose_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hTLHandle              | @ref KYVP_PCI_INTERFACE_HANDLE    | System module handle to close                             |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_IFClose_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_IFClose_Args_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);

} KYVPLibTL_IFClose_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_IFGetInfo_Args_VERSION = 1;
#else
#define KYVPLibTL_IFGetInfo_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_IFGetInfo_Args KYVPLibTL_IFGetInfo_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hIFHandle              | KYVP_PCI_INTERFACE_HANDLE         | Interface module to work on.                              |           |
 * | eInfoCmd               | @ref KYVP_INTERFACE_INFO_CMD      | Information to be retrieved                               |           |
 * | pType                  | @ref KYVP_INFO_DATATYPE *         | Pointer to data type of the pBuffer content               |           |
 * | pBuffer                | void*                             | Pointer to a user allocated buffer                        |           |
 * | piSize*                | size_t*                           | Number of bytes filled by the function
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_IFGetInfo_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_IFGetInfo_Args_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    KYVP_INTERFACE_INFO_CMD                 eInfoCmd    KYVP_MEMBER_DEFAULT(KYVP_INTERFACE_INFO_CMD_ID);
    KYVP_INFO_DATATYPE*                     pType       KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_IFGetInfo_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_IFGetNumDevices_Args_VERSION = 1;
#else
#define KYVPLibTL_IFGetNumDevices_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_IFGetNumDevices_Args KYVPLibTL_IFGetNumDevices_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hIFHandle              | KYVP_PCI_INTERFACE_HANDLE         | Interface module to work on.                              |           |
 * | pNumDevices            | uint32_t*                         | Number of devices                                         |           |
 * 
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_IFGetNumDevices_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_IFGetNumDevices_Args_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint32_t*                               pNumDevices KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_IFGetNumDevices_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_IFGetDeviceID_Args_VERSION = 1;
#else
#define KYVPLibTL_IFGetDeviceID_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_IFGetDeviceID_Args KYVPLibTL_IFGetDeviceID_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hIFHandle              | @ref KYVP_PCI_INTERFACE_HANDLE    | Interface module to work on.                              |           |
 * | iIndex                 | uint32_t                          | Device index                                              |           |
 * | pszID                  | char*                             | Pointer to a user allocated C string buffer to receive the Device module ID at the given iIndex | |
 * | piSize                 | size_t*                           | Minimal size of pBuffer in bytes to hold all information  |           |
 * 
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_IFGetDeviceID_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_IFGetDeviceID_Args_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    uint32_t                                iIndex      KYVP_MEMBER_DEFAULT(0);
    char*                                   pszID       KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_IFGetDeviceID_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_IFUpdateDeviceList_Args_VERSION = 1;
#else
#define KYVPLibTL_IFUpdateDeviceList_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_IFUpdateDeviceList_Args KYVPLibTL_IFUpdateDeviceList_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hIFHandle              | @ref KYVP_PCI_INTERFACE_HANDLE    | Interface module to work on.                              |           |
 * | pbChanged              | @ref KY_BOOL*                     | Contains KY_TRUE if the internal list was changed and KY_FALSE otherwise. | |
 * | uTimeout               | uint64_t                          | Timeout in ms                                             |           |
 * 
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_IFUpdateDeviceList_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_IFUpdateDeviceList_Args_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    KY_BOOL*                                pbChanged   KYVP_MEMBER_DEFAULT(nullptr);
    uint64_t                                uTimeout    KYVP_MEMBER_DEFAULT(0);

} KYVPLibTL_IFUpdateDeviceList_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_IFGetDeviceInfo_Args_VERSION = 1;
#else
#define KYVPLibTL_IFGetDeviceInfo_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_IFGetDeviceInfo_Args KYVPLibTL_IFGetDeviceInfo_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hIFHandle              | @ref KYVP_PCI_INTERFACE_HANDLE    | Interface module to work on.                              |           |
 * | pszIfaceID             | const char*                       | Unique ID of the device to inquire information about.     |           |
 * | eInfoCmd               | @ref KYVP_DEVICE_INFO_CMD         | Information to be retrieved                               |           |
 * | pType                  | @ref KYVP_INFO_DATATYPE*          | Data type of the pBuffer content                          |           |
 * | pBuffer                | void*                             | Pointer to a user allocated buffer                        |           |
 * | piSize                 | size_t*                           | Number of bytes filled by the function                    |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_IFGetDeviceInfo_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_IFGetDeviceInfo_Args_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    const char*                             pszIfaceID  KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_DEVICE_INFO_CMD                    eInfoCmd    KYVP_MEMBER_DEFAULT(KYVP_DEVICE_INFO_CMD_ID);
    KYVP_INFO_DATATYPE*                     pType       KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_IFGetDeviceInfo_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_IFOpenDevice_Args_VERSION = 1;
#else
#define KYVPLibTL_IFOpenDevice_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_IFOpenDevice_Args KYVPLibTL_IFOpenDevice_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hIFHandle              | @ref KYVP_PCI_INTERFACE_HANDLE    | Interface module to work on.                              |           |
 * | pszDeviceID            | const char*                       | Unique device ID to open as a 0-terminated C string.      |           |
 * | iOpenFlags             | @ref KYVP_DEVICE_ACCESS_FLAGS     | Configures the open process                               |           |
 * | phDeviceHandle         | @ref KYVP_DEVICE_HANDLE*          | Device handle of the newly created Device module.         |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_IFOpenDevice_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_IFOpenDevice_Args_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle       KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    const char*                             pszDeviceID     KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_DEVICE_ACCESS_FLAGS                iOpenFlags      KYVP_MEMBER_DEFAULT(KYVP_DEVICE_ACCESS_FLAGS_UNKNOWN);
    KYVP_DEVICE_HANDLE*                     phDeviceHandle  KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_IFOpenDevice_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_IFREGISTEREVENT_ARGS_VERSION = 1;
#else
#define KYVPLIBTL_IFREGISTEREVENT_ARGS_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_IFRegisterEvent_Args KYVPLibTL_IFRegisterEvent_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hIFHandle              | @ref KYVP_PCI_INTERFACE_HANDLE    | Module handle to access to register event to              |           |
 * | iEventID               | @ref KYVP_EVENT_TYPE              | Event type to register                                    |           |
 * | phEvent                | @ref KYVP_EVENT_HANDLE*           | New handle to an event object to work with                |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_IFRegisterEvent_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_IFREGISTEREVENT_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    KYVP_EVENT_TYPE                         iEventID    KYVP_MEMBER_DEFAULT(KYVP_EVENT_TYPE_INVALID);
    KYVP_EVENT_HANDLE*                      phEvent     KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_IFRegisterEvent_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_IFUNREGISTEREVENT_ARGS_VERSION = 1;
#else
#define KYVPLIBTL_IFUNREGISTEREVENT_ARGS_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_IFUnregisterEvent_Args KYVPLibTL_IFUnregisterEvent_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hIFHandle              | @ref KYVP_PCI_INTERFACE_HANDLE    | Module handle to access to unregister event from          |           |
 * | iEventID               | @ref KYVP_EVENT_TYPE              | Event type to register                                    |           |
 * | phEvent                | @ref KYVP_EVENT_HANDLE*           | New handle to an event object to work with                |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_IFUnregisterEvent_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLIBTL_IFUNREGISTEREVENT_ARGS_VERSION);
    KYVP_PCI_INTERFACE_HANDLE               hIFHandle   KYVP_MEMBER_DEFAULT(KYVP_PCI_INTERFACE_HANDLE_INVALID);
    KYVP_EVENT_TYPE                         iEventID    KYVP_MEMBER_DEFAULT(KYVP_EVENT_TYPE_INVALID);

} KYVPLibTL_IFUnregisterEvent_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DevGetPort_Args_VERSION = 1;
#else
#define KYVPLibTL_DevGetPort_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DevGetPort_Args KYVPLibTL_DevGetPort_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDevHandle             | @ref KYVP_DEVICE_HANDLE           | Device module to work on.                                 |           |
 * | phRemoteDeviceHandle   | @ref KYVP_REMOTE_DEVICE_HANDLE*   | Port handle for the remote device                         |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DevGetPort_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPLibTL_DevGetPort_Args_VERSION);
    KYVP_DEVICE_HANDLE                      hDevHandle              KYVP_MEMBER_DEFAULT(KYVP_DEVICE_HANDLE_INVALID);
    KYVP_REMOTE_DEVICE_HANDLE*              phRemoteDeviceHandle    KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DevGetPort_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DevGetNumDataStreams_Args_VERSION = 1;
#else
#define KYVPLibTL_DevGetNumDataStreams_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DevGetNumDataStreams_Args KYVPLibTL_DevGetNumDataStreams_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDevHandle             | @ref KYVP_DEVICE_HANDLE           | Device module to work on.                                 |           |
 * | piNumDataStreams       | uint32_t*                         | Number of data stream on this Device module.              |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DevGetNumDataStreams_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYVPLibTL_DevGetNumDataStreams_Args_VERSION);
    KYVP_DEVICE_HANDLE                      hDevHandle              KYVP_MEMBER_DEFAULT(KYVP_DEVICE_HANDLE_INVALID);
    uint32_t*                               piNumDataStreams        KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DevGetNumDataStreams_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DevGetDataStreamID_Args_VERSION = 1;
#else
#define KYVPLibTL_DevGetDataStreamID_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DevGetDataStreamID_Args KYVPLibTL_DevGetDataStreamID_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDevHandle             | @ref KYVP_DEVICE_HANDLE           | Device module to work on.                                 |           |
 * | iIndex                 | uint32_t                          | Zero-based index of the data stream on this device        |           |
 * | sDataStreamID          | char*                             | Pointer to a user allocated C string buffer               |           |
 * | piSize                 | size_t*                           | Number of bytes filled by the function                    |           |
 * 
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DevGetDataStreamID_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_DevGetDataStreamID_Args_VERSION);
    KYVP_DEVICE_HANDLE                      hDevHandle      KYVP_MEMBER_DEFAULT(KYVP_DEVICE_HANDLE_INVALID);
    uint32_t                                iIndex          KYVP_MEMBER_DEFAULT(0);
    char*                                   sDataStreamID   KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize          KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DevGetDataStreamID_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DevOpenDataStream_Args_VERSION = 1;
#else
#define KYVPLibTL_DevOpenDataStream_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DevOpenDataStream_Args KYVPLibTL_DevOpenDataStream_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDevHandle             | @ref KYVP_DEVICE_HANDLE           | Device module to work on.                                 |           |
 * | pszDataStreamID        | const char*                       | Unique data stream ID to open as a 0-terminated C string  |           |
 * | phDataStream           | KYVP_STREAM_HANDLE*               | Data Stream module handle of the newly created stream     |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DevOpenDataStream_Args
{
    uint32_t                                version             KYVP_MEMBER_DEFAULT(KYVPLibTL_DevOpenDataStream_Args_VERSION);
    KYVP_DEVICE_HANDLE                      hDevHandle          KYVP_MEMBER_DEFAULT(KYVP_DEVICE_HANDLE_INVALID);
    const char*                             pszDataStreamID     KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_STREAM_HANDLE*                     phDataStream        KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DevOpenDataStream_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DevGetInfo_Args_VERSION = 1;
#else
#define KYVPLibTL_DevGetInfo_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DevGetInfo_Args KYVPLibTL_DevGetInfo_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDevHandle             | @ref KYVP_DEVICE_HANDLE           | Device module to work on.                                 |           |
 * | iInfoCmd               | @ref KYVP_DEVICE_INFO_CMD         | Information to be retrieved                               |           |
 * | piType                 | @ref KYVP_INFO_DATATYPE*          | Data type of the pBuffer content                          |           |
 * | pBuffer                | void*                             | Pointer to a user allocated buffer                        |           |
 * | piSize                 | size_t*                           | Number of bytes filled by the function                    |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DevGetInfo_Args
{
    uint32_t                                version             KYVP_MEMBER_DEFAULT(KYVPLibTL_DevGetInfo_Args_VERSION);
    KYVP_DEVICE_HANDLE                      hDevHandle          KYVP_MEMBER_DEFAULT(KYVP_DEVICE_HANDLE_INVALID);
    KYVP_DEVICE_INFO_CMD                    iInfoCmd            KYVP_MEMBER_DEFAULT(KYVP_DEVICE_INFO_CMD::KYVP_DEVICE_INFO_CMD_ID);
    KYVP_INFO_DATATYPE*                     piType              KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pBuffer             KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize              KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DevGetInfo_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DevClose_Args_VERSION = 1;
#else
#define KYVPLibTL_DevClose_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DevClose_Args KYVPLibTL_DevClose_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDevHandle             | @ref KYVP_DEVICE_HANDLE           | Device module handle to close                             |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DevClose_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_DevClose_Args_VERSION);
    KYVP_DEVICE_HANDLE                      hDevHandle  KYVP_MEMBER_DEFAULT(KYVP_DEVICE_HANDLE_INVALID);

} KYVPLibTL_DevClose_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSAnnounceBuffer_Args_VERSION = 1;
#else
#define KYVPLibTL_DSAnnounceBuffer_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSAnnounceBuffer_Args KYVPLibTL_DSAnnounceBuffer_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Data Stream module to work on.                            |           |
 * | pBuffer                | void*                             | Pointer to buffer memory to announce                      |           |
 * | iSize                  | size_t                            | Size of the pBuffer in bytes                              |           |
 * | pPrivate               | void*                             | Pointer to private data which will be passed to the GenTL Consumer on New Buffer events. This parameter may be NULL |     |
 * | phBuffer               | @ref KYVP_BUFFER_HANDLE*          | Buffer module handle of the newly announced buffer. I     |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSAnnounceBuffer_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_DSAnnounceBuffer_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle   KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t                                  iSize       KYVP_MEMBER_DEFAULT(0);
    void*                                   pPrivate    KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_BUFFER_HANDLE*                     phBuffer    KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DSAnnounceBuffer_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSAllocAndAnnounceBuffer_Args_VERSION = 1;
#else
#define KYVPLibTL_DSAllocAndAnnounceBuffer_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSAllocAndAnnounceBuffer_Args KYVPLibTL_DSAllocAndAnnounceBuffer_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Data Stream module to work on.                            |           |
 * | iSize                  | size_t                            | Size of the buffer in bytes                               |           |
 * | pPrivate               | void*                             | Pointer to private data which will be passed to the Consumer on New Buffer events. This parameter may be NULL |   |
 * | phBuffer               | @ref KYVP_BUFFER_HANDLE*          | Buffer module handle of the newly announced buffer        |           |

 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSAllocAndAnnounceBuffer_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_DSAllocAndAnnounceBuffer_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle   KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    size_t                                  iSize       KYVP_MEMBER_DEFAULT(0);
    void*                                   pPrivate    KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_BUFFER_HANDLE*                     phBuffer    KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DSAllocAndAnnounceBuffer_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSStartAcquisition_Args_VERSION = 1;
#else
#define KYVPLibTL_DSStartAcquisition_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSStartAcquisition_Args KYVPLibTL_DSStartAcquisition_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Data Stream module to work on                             |           |
 * | iStartFlags            | @ref KYVP_ACQ_START_FLAGS         | As defined in @ref KYVP_ACQ_START_FLAGS                   |           |
 * | iNumToAcquire          | uint64_t                          | Sets the number of filled/delivered buffers after which the acquisition engine stops automatically if set 0 the acquisition continues until a call to KYVPLibTL_DSStopAcquisition |   |
 * | 
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSStartAcquisition_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_DSStartAcquisition_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle       KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    KYVP_ACQ_START_FLAGS                    iStartFlags     KYVP_MEMBER_DEFAULT(KYVP_ACQ_START_FLAGS_DEFAULT);
    uint64_t                                iNumToAcquire   KYVP_MEMBER_DEFAULT(0);

} KYVPLibTL_DSStartAcquisition_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSStopAcquisition_Args_VERSION = 1;
#else
#define KYVPLibTL_DSStopAcquisition_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSStopAcquisition_Args KYVPLibTL_DSStopAcquisition_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Data Stream module to work on                             |           |
 * | iStopFlags             | @ref KYVP_ACQ_STOP_FLAGS          | Stops the acquisition as defined in @ref KYVP_ACQ_STOP_FLAGS |        |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSStopAcquisition_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_DSStopAcquisition_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle   KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    KYVP_ACQ_STOP_FLAGS                     iStopFlags  KYVP_MEMBER_DEFAULT(KYVP_ACQ_STOP_FLAGS_DEFAULT);

} KYVPLibTL_DSStopAcquisition_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSGetInfo_Args_VERSION = 1;
#else
#define KYVPLibTL_DSGetInfo_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSGetInfo_Args KYVPLibTL_DSGetInfo_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Data Stream module to work on                             |           |
 * | eInfoCmd               | @ref KYVP_STREAM_INFO_CMD         | Information to be retrieved                               |           |
 * | pType                  | @ref KYVP_INFO_DATATYPE*          | Data type of the pBuffer content                          |           |
 * | pBuffer                | void*                             | Pointer to a user allocated buffer to receive the requested information   |       |
 * | piSize                 | size_t*                           | number of bytes filled by the function                    |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSGetInfo_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_DSGetInfo_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle   KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    KYVP_STREAM_INFO_CMD                    eInfoCmd    KYVP_MEMBER_DEFAULT(KYVP_STREAM_INFO_CMD_INVALID);
    KYVP_INFO_DATATYPE*                     pType       KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pBuffer     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize      KYVP_MEMBER_DEFAULT(nullptr);
} KYVPLibTL_DSGetInfo_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLIBTL_DSGETBUFFEREID_STRUCT_VERSION = 1;
#else
#define KYVPLIBTL_DSGETBUFFEREID_STRUCT_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSGetBufferID_Args KYVPLibTL_DSGetBufferID_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Data Stream module to work on                             |           |
 * | iIndex                 | uint32_t                          | Zero-based index of the buffer on this data stream        |           |
 * | ppBufferHandle         | @ref KYVP_BUFFER_HANDLE*          | Buffer module handle of the given iIndex                  |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSGetBufferID_Args
{
    uint32_t                                version             KYVP_MEMBER_DEFAULT(KYVPLIBTL_DSGETBUFFEREID_STRUCT_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle           KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    uint32_t                                iIndex              KYVP_MEMBER_DEFAULT(0);
    KYVP_BUFFER_HANDLE*                     ppBufferHandle      KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DSGetBufferID_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSClose_Args_VERSION = 1;
#else
#define KYVPLibTL_DSClose_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSClose_Args KYVPLibTL_DSClose_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Data Stream module to work on                             |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSClose_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_DSClose_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle   KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);

} KYVPLibTL_DSClose_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSRevokeBuffer_Args_VERSION = 1;
#else
#define KYVPLibTL_DSRevokeBuffer_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSRevokeBuffer_Args KYVPLibTL_DSRevokeBuffer_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Data Stream module to work on                             |           |
 * | hBufferHandle          | @ref KYVP_BUFFER_HANDLE           | Buffer handle to revoke                                   |           |
 * | ppBuffer               | void**                            | Pointer to the buffer memory This is for convenience ifConsumer allocated memory is used which is to befreed. If the buffer was allocated by the GenTL Producer NULL is to be returned. If the parameter is set to NULL it is ignored. | |
 * | ppPrivate              | void**                            | Pointer to the user data pointer given in the announce function. If the parameter is set to NULL it is ignored. |  |

 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSRevokeBuffer_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_DSRevokeBuffer_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle       KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    KYVP_BUFFER_HANDLE                      hBufferHandle   KYVP_MEMBER_DEFAULT(KYVP_BUFFER_HANDLE_INVALID);
    void**                                  ppBuffer        KYVP_MEMBER_DEFAULT(nullptr);
    void**                                  ppPrivate       KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DSRevokeBuffer_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSQueueBuffer_Args_VERSION = 1;
#else
#define KYVPLibTL_DSQueueBuffer_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSQueueBuffer_Args KYVPLibTL_DSQueueBuffer_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Data Stream module to work on                             |           |
 * | hBufferHandle          | @ref KYVP_BUFFER_HANDLE           | Buffer handle to queue                                    |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSQueueBuffer_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_DSQueueBuffer_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle       KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    KYVP_BUFFER_HANDLE                      hBufferHandle   KYVP_MEMBER_DEFAULT(KYVP_BUFFER_HANDLE_INVALID);

} KYVPLibTL_DSQueueBuffer_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSGetBufferInfo_Args_VERSION = 1;
#else
#define KYVPLibTL_DSGetBufferInfo_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSGetBufferInfo_Args KYVPLibTL_DSGetBufferInfo_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Data Stream module to work on                             |           |
 * | hBufferHandle          | @ref KYVP_BUFFER_HANDLE           | Buffer handle to retrieve information about               |           |
 * | iInfoCmd               | @ref KYVP_BUFFER_INFO_CMD         | Information to be retrieved                               |           |
 * | piType                 | @refKYVP_INFO_DATATYPE*           | Data type of the pBuffer content                          |           |
 * | pBuffer                | void*                             | Pointer to a user allocated buffer to receive the requested information |     |
 * | piSize                 | size_t*                           |  number of bytes filled by the function                   |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSGetBufferInfo_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_DSGetBufferInfo_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle       KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    KYVP_BUFFER_HANDLE                      hBufferHandle   KYVP_MEMBER_DEFAULT(KYVP_BUFFER_HANDLE_INVALID);
    KYVP_BUFFER_INFO_CMD                    iInfoCmd        KYVP_MEMBER_DEFAULT(KYVP_BUFFER_INFO_CMD_BASE);
    KYVP_INFO_DATATYPE*                     piType          KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pBuffer         KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize          KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DSGetBufferInfo_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSRegisterEvent_Args_VERSION = 1;
#else
#define KYVPLibTL_DSRegisterEvent_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSRegisterEvent_Args KYVPLibTL_DSRegisterEvent_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Module handle to access to register event to              |           |
 * | iEventID               | @ref KYVP_EVENT_TYPE              | Event type to register                                    |           |
 * | phEvent                | @ref KYVP_EVENT_HANDLE*           | New handle to an event object to work with                |           | 
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSRegisterEvent_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_DSRegisterEvent_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle   KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    KYVP_EVENT_TYPE                         iEventID    KYVP_MEMBER_DEFAULT(KYVP_EVENT_TYPE_INVALID);
    KYVP_EVENT_HANDLE*                      phEvent     KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_DSRegisterEvent_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_DSUnregisterEvent_Args_VERSION = 1;
#else
#define KYVPLibTL_DSRegisterEvent_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_DSUnregisterEvent_Args KYVPLibTL_DSUnregisterEvent_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hDSHandle              | @ref KYVP_STREAM_HANDLE           | Module handle to access to unregister event from          |           |
 * | iEventID               | @ref KYVP_EVENT_TYPE              | Event type to register                                    |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_DSUnregisterEvent_Args
{
    uint32_t                                version     KYVP_MEMBER_DEFAULT(KYVPLibTL_DSUnregisterEvent_Args_VERSION);
    KYVP_STREAM_HANDLE                      hDSHandle   KYVP_MEMBER_DEFAULT(KYVP_STREAM_HANDLE_INVALID);
    KYVP_EVENT_TYPE                         iEventID    KYVP_MEMBER_DEFAULT(KYVP_EVENT_TYPE_INVALID);

} KYVPLibTL_DSUnregisterEvent_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_EventGetData_Args_VERSION = 1;
#else
#define KYVPLibTL_EventGetData_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_EventGetData_Args KYVPLibTL_EventGetData_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hEventHandle           | @ref KYVP_EVENT_HANDLE            | PEvent handle to wait for                                 |           |
 * | void*                  | pBuffer                           | Pointer to a user allocated buffer                        |           |
 * | piSize                 | size_t*                           | Size of the provided pBuffer in bytes                     |           |
 * | iTimeout               | uint64_t                          | Timeout for the wait in ms                                |           |   
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_EventGetData_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_EventGetData_Args_VERSION);
    KYVP_EVENT_HANDLE                       hEventHandle    KYVP_MEMBER_DEFAULT(KYVP_EVENT_HANDLE_INVALID);
    void*                                   pBuffer         KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize          KYVP_MEMBER_DEFAULT(nullptr);
    uint64_t                                iTimeout        KYVP_MEMBER_DEFAULT(0);

} KYVPLibTL_EventGetData_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_EventGetDataInfo_Args_VERSION = 1;
#else
#define KYVPLibTL_EventGetDataInfo_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_EventGetDataInfo_Args KYVPLibTL_EventGetDataInfo_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hEventHandle           | @ref KYVP_EVENT_HANDLE            | Event handle to parse data from                           |           |
 * | pInBuffer              | const void*                       | Pointer to a buffer containing event data                 |           |
 * | iInSize                | size_t                            | Size of the provided pInBuffer in bytes                   |           |
 * | iInfoCmd               | @ref KYVP_EVENT_DATA_INFO_CMD     | Information to be retrieved                               |           |
 * | piType                 | @ref KYVP_INFO_DATATYPE*          | Data type of the pOutBuffer content                       |           |
 * | pOutBuffer             | void*                             | Pointer to a user allocated buffer                        |           |
 * | piOutSize              | size_t*                           | number of bytes filled by the function                    |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_EventGetDataInfo_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_EventGetDataInfo_Args_VERSION);
    KYVP_EVENT_HANDLE                       hEventHandle    KYVP_MEMBER_DEFAULT(KYVP_EVENT_HANDLE_INVALID);
    const void*                             pInBuffer       KYVP_MEMBER_DEFAULT(nullptr);
    size_t                                  iInSize         KYVP_MEMBER_DEFAULT(0);
    KYVP_EVENT_DATA_INFO_CMD                iInfoCmd        KYVP_MEMBER_DEFAULT(KYVP_EVENT_DATA_INFO_CMD_ID);
    KYVP_INFO_DATATYPE*                     piType          KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pOutBuffer      KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piOutSize       KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_EventGetDataInfo_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_EventGetInfo_Args_VERSION = 1;
#else
#define KYVPLibTL_EventGetInfo_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_EventGetInfo_Args KYVPLibTL_EventGetInfo_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hEventHandle           | @ref KYVP_EVENT_HANDLE            | Event handle to parse data from                           |           |
 * | iInfoCmd               | @ref KYVP_EVENT_INFO_CMD          | Information to be retrieved                               |           |
 * | piType                 | @ref KYVP_INFO_DATATYPE*          | Data type of the pBuffer content                          |           |
 * | pBuffer                | void*                             | Pointer to a user allocated buffer                        |           |
 * | piSize                 | size_t*                           | Number of bytes filled by the function                    |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_EventGetInfo_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_EventGetInfo_Args_VERSION);
    KYVP_EVENT_HANDLE                       hEventHandle    KYVP_MEMBER_DEFAULT(KYVP_EVENT_HANDLE_INVALID);
    KYVP_EVENT_INFO_CMD                     iInfoCmd        KYVP_MEMBER_DEFAULT(KYVP_EVENT_INFO_CMD_TYPE);
    KYVP_INFO_DATATYPE*                     piType          KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pBuffer         KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize          KYVP_MEMBER_DEFAULT(nullptr);

} KYVPLibTL_EventGetInfo_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_EventFlush_Args_VERSION = 1;
#else
#define KYVPLibTL_EventFlush_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_EventFlush_Args KYVPLibTL_EventFlush_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hEventHandle           | @ref KYVP_EVENT_HANDLE            | Event handle                                              |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_EventFlush_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_EventFlush_Args_VERSION);
    KYVP_EVENT_HANDLE                       hEventHandle    KYVP_MEMBER_DEFAULT(KYVP_EVENT_HANDLE_INVALID);

} KYVPLibTL_EventFlush_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYVPLibTL_EventKill_Args_VERSION = 1;
#else
#define KYVPLibTL_EventKill_Args_VERSION 1
#endif
/**
 * @page KYVPLibTL_Structs KYVPLibTL Structs
 * @section KYVPLibTL_EventKill_Args KYVPLibTL_EventKill_Args
 * | Structure Field        | Type                              | Description                                               | Remarks   |
 * | -------------          | -----                             | -----------                                               | ----      |
 * | version                | uint32_t                          | struct version                                            |           |
 * | hEventHandle           | @ref KYVP_EVENT_HANDLE            | Event handle                                              |           |
 */
#pragma pack(push, 1)/** @cond INTERNAL */
typedef struct _KYVPLibTL_EventKill_Args
{
    uint32_t                                version         KYVP_MEMBER_DEFAULT(KYVPLibTL_EventKill_Args_VERSION);
    KYVP_EVENT_HANDLE                       hEventHandle    KYVP_MEMBER_DEFAULT(KYVP_EVENT_HANDLE_INVALID);

} KYVPLibTL_EventKill_Args;
#pragma pack(pop)/** @endcond */

//////////////////////////////////////////////////////////////////////

//////////////////////////////////////////////////////////////////////
/// KYVPLIBTL API FUNCTIONS
//////////////////////////////////////////////////////////////////////

/**
 * @defgroup SystemModule System module
 * @{
 */

/**
 * @section KYVPLibTL_InitLib KYVPLibTL_InitLib()
 * This function must be called prior to any other function call to allow global initialization of
    the GenTL Producer driver. This function is necessary since automated initialization
    functionality like within DllMain on MS Windows platforms is very limited
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_InitLib() @endcode
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVP_CALLCONV KYVPLibTL_InitLib(void);

/**
 * @section KYVPLibTL_CloseLib KYVPLibTL_CloseLib()
 * Closes the System module associated with the given hSystem handle. This closes the whole
    GenTL Producer driver and frees all resources
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_CloseLib() @endcode
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVP_CALLCONV KYVPLibTL_CloseLib(void);

/// @}

/**
 * @defgroup LowLevelFunctions Low level functions
 * @{
 */

 /**
  * @section KYVPLibTL_GetPortURL KYVPLibTL_GetPortURL()
  * GCGetPortURL retrieves a URL list with the XML description for the given hPort
  * @subsection C C
  * @code{.c} KY_RESULT KYVPLibTL_GetPortURL(KYVPLibTL_GetPortUrl_Args* _pArgs) @endcode
  * @param [in,out] _pArgs pointer to @ref KYVPLibTL_GetPortUrl_Args
  * @return KY_RESULT Status and error report
  */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_GetPortURL(KYVPLibTL_GetPortUrl_Args* _pArgs);

/**
 * @section KYVPLibTL_ReadPort KYVPLibTL_ReadPort()
 * Reads a number of bytes from a given iAddress from the specified hPort.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_ReadPort(KYVPLibTL_ReadPort_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_ReadPort_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_ReadPort(KYVPLibTL_ReadPort_Args* _pArgs);

/**
 * @section KYVPLibTL_WritePort KYVPLibTL_WritePort()
 * Writes a number of bytes at the given iAddress to the specified hPort.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_WritePort(KYVPLibTL_WritePort_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_WritePort_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_WritePort(KYVPLibTL_WritePort_Args* _pArgs);


/**
 * @section KYVPLibTL_PCIInterface_GetPortURL KYVPLibTL_PCIInterface_GetPortURL()
 * GCGetPortURL retrieves a URL list with the XML description for the given hPort
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_PCIInterface_GetPortURL(KYVPLibTL_PCIInterface_GetPortUrl_Args* _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_PCIInterface_GetPortUrl_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_PCIInterface_GetPortURL(KYVPLibTL_PCIInterface_GetPortUrl_Args* _pArgs);

/**
 * @section KYVPLibTL_PCIInterface_ReadPort KYVPLibTL_PCIInterface_ReadPort()
 * Reads a number of bytes from a given iAddress from the specified hPort.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_PCIInterface_ReadPort(KYVPLibTL_PCIInterface_ReadPort_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_PCIInterface_ReadPort_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_PCIInterface_ReadPort(KYVPLibTL_PCIInterface_ReadPort_Args* _pArgs);

/**
 * @section KYVPLibTL_PCIInterface_WritePort KYVPLibTL_PCIInterface_WritePort()
 * Writes a number of bytes at the given iAddress to the specified hPort.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_PCIInterface_WritePort(KYVPLibTL_PCIInterface_WritePort_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_PCIInterface_WritePort_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_PCIInterface_WritePort(KYVPLibTL_PCIInterface_WritePort_Args* _pArgs);


/**
 * @section KYVPLibTL_Device_GetPortURL KYVPLibTL_Device_GetPortURL()
 * Retrieves a URL list with the source description for the given hPort
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_Device_GetPortURL(KYVPLibTL_Device_GetPortUrl_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_Device_GetPortUrl_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Device_GetPortURL(KYVPLibTL_Device_GetPortUrl_Args* _pArgs);

/**
* @section KYVPLibTL_Device_ReadPort KYVPLibTL_Device_ReadPort()
* Reads a number of bytes from a given iAddress.
* @subsection C C
* @code{.c} KY_RESULT KYVPLibTL_Device_ReadPort(KYVPLibTL_Device_ReadPort_Args _pArgs) @endcode
* @param [in,out] _pArgs pointer to @ref KYVPLibTL_Device_ReadPort_Args
* @return KY_RESULT Status and error report
*/
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Device_ReadPort(KYVPLibTL_Device_ReadPort_Args* _pArgs);

/**
 * @section KYVPLibTL_Device_WritePort KYVPLibTL_Device_WritePort()
 * Writes a number of bytes at the given iAddress.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_Device_WritePort(KYVPLibTL_Device_WritePort_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_Device_WritePort_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Device_WritePort(KYVPLibTL_Device_WritePort_Args* _pArgs);


/**
 * @section KYVPLibTL_RemoteDevice_GetPortURL KYVPLibTL_RemoteDevice_GetPortURL()
 * Retrieves a URL list with the source description.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_RemoteDevice_GetPortURL(KYVPLibTL_RemoteDevice_GetPortUrl_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_RemoteDevice_GetPortUrl_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_RemoteDevice_GetPortURL(KYVPLibTL_RemoteDevice_GetPortUrl_Args* _pArgs);

/**
 * @section KYVPLibTL_RemoteDevice_ReadPort KYVPLibTL_RemoteDevice_ReadPort()
 * Reads a number of bytes from a given iAddress.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_RemoteDevice_ReadPort(KYVPLibTL_RemoteDevice_ReadPort_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_RemoteDevice_ReadPort_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_RemoteDevice_ReadPort(KYVPLibTL_RemoteDevice_ReadPort_Args* _pArgs);

/**
 * @section KYVPLibTL_RemoteDevice_WritePort KYVPLibTL_RemoteDevice_WritePort()
 * Writes a number of bytes at the given iAddress.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_RemoteDevice_WritePort(KYVPLibTL_RemoteDevice_WritePort_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_RemoteDevice_WritePort_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_RemoteDevice_WritePort(KYVPLibTL_RemoteDevice_WritePort_Args* _pArgs);


/**
 * @section KYVPLibTL_Stream_GetPortURL KYVPLibTL_Stream_GetPortURL()
 *  Retrieves a URL list with the source description.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_Stream_GetPortURL(KYVPLibTL_Stream_GetPortUrl_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_Stream_GetPortUrl_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Stream_GetPortURL(KYVPLibTL_Stream_GetPortUrl_Args* _pArgs);

/**
 * @section KYVPLibTL_Stream_ReadPort KYVPLibTL_Stream_ReadPort()
 * Reads a number of bytes from a given iAddress.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_Stream_ReadPort(KYVPLibTL_Stream_ReadPort_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_Stream_ReadPort_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Stream_ReadPort(KYVPLibTL_Stream_ReadPort_Args* _pArgs);

/**
 * @section KYVPLibTL_Stream_WritePort KYVPLibTL_Stream_WritePort()
 * Writes a number of bytes at the given iAddress.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_Stream_WritePort(KYVPLibTL_Stream_WritePort_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_Stream_WritePort_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Stream_WritePort(KYVPLibTL_Stream_WritePort_Args* _pArgs);

/**
 * @}
 */
 /// @defgroup SystemModule System module
 /// @{
/**
 * @page KYVPLibTL_Functions_System
 * @section KYVPLibTL_TLOpen KYVPLibTL_TLOpen()
 * Opens the System module and puts the instance.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_TLOpen(KYVPLibTL_TLOpen_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_TLOpen_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLOpen(KYVPLibTL_TLOpen_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_System
 * @section KYVPLibTL_TLClose KYVPLibTL_TLClose()
 * Close the System module and puts the instance.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_TLClose(KYVPLibTL_TLClose_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_TLClose_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLClose(KYVPLibTL_TLClose_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_System
 * @section KYVPLibTL_TLGetInfo KYVPLibTL_TLGetInfo()
 * Inquire information about the System module.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_TLGetInfo(KYVPLibTL_TLGetInfo_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_TLGetInfo_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLGetInfo(KYVPLibTL_TLGetInfo_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_System
 * @section KYVPLibTL_TLGetNumInterfaces KYVPLibTL_TLGetNumInterfaces()
 * Queries the number of available interfaces on this System module.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_TLGetNumInterfaces(KYVPLibTL_TLGetNumInterfaces_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_TLGetNumInterfaces_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLGetNumInterfaces(KYVPLibTL_TLGetNumInterfaces_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_System
 * @section KYVPLibTL_TLGetInterfaceID KYVPLibTL_TLGetInterfaceID()
 * Queries the unique ID of the interface at iIndex in the internal interface list.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_TLGetInterfaceID(KYVPLibTL_TLGetInterfaceID_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_TLGetInterfaceID_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLGetInterfaceID(KYVPLibTL_TLGetInterfaceID_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_System
 * @section KYVPLibTL_TLGetInterfaceInfo KYVPLibTL_TLGetInterfaceInfo()
 * Inquire information about an interface on the given System module.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_TLGetInterfaceInfo(KYVPLibTL_TLGetInterfaceInfo_Struct _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_TLGetInterfaceInfo_Struct
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLGetInterfaceInfo(KYVPLibTL_TLGetPCIInterfaceInfo_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_System
 * @section KYVPLibTL_TLOpenInterface KYVPLibTL_TLOpenInterface()
 * Opens the given interface ID.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_TLOpenInterface(KYVPLibTL_TLOpenInterface_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_TLOpenInterface_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLOpenInterface(KYVPLibTL_TLOpenInterface_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_System
 * @section KYVPLibTL_TLUpdateInterfaceList KYVPLibTL_TLUpdateInterfaceList()
 * Updates the internal list of available interfaces.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_TLUpdateInterfaceList(KYVPLibTL_TLUpdateInterfaceList_Struct _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_TLUpdateInterfaceList_Struct
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLUpdateInterfaceList(KYVPLibTL_TLUpdatePCIInterfaceList_Args* _pArgs);

/// @}


/// @defgroup InterfaceModule Interface module
/// @{

/**
 * @page KYVPLibTL_Functions_Interface
 * @section KYVPLibTL_IFClose KYVPLibTL_IFClose()
 * Closes the Interface module.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_IFClose(KYVPLibTL_IFClose_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_IFClose_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFClose(KYVPLibTL_IFClose_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Interface
 * @section KYVPLibTL_IFGetInfo KYVPLibTL_IFGetInfo()
 * Inquires information about the Interface module.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_IFGetInfo(KYVPLibTL_IFGetInfo_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_IFGetInfo_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFGetInfo(KYVPLibTL_IFGetInfo_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Interface
 * @section KYVPLibTL_IFGetNumDevices KYVPLibTL_IFGetNumDevices()
 * Queries the number of available devices on this Interface module.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_IFGetNumDevices(KYVPLibTL_IFGetNumDevices_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_IFGetNumDevices_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFGetNumDevices(KYVPLibTL_IFGetNumDevices_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Interface
 * @section KYVPLibTL_IFGetDeviceID KYVPLibTL_IFGetDeviceID()
 * Queries the unique ID of the device at iIndex in the internal device list.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_IFGetDeviceID(KYVPLibTL_IFGetDeviceID_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_IFGetDeviceID_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFGetDeviceID(KYVPLibTL_IFGetDeviceID_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Interface
 * @section KYVPLibTL_IFUpdateDeviceList KYVPLibTL_IFUpdateDeviceList()
 * Updates the internal list of available devices.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_IFUpdateDeviceList(KYVPLibTL_IFUpdateDeviceList_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_IFUpdateDeviceList Struct
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFUpdateDeviceList(KYVPLibTL_IFUpdateDeviceList_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Interface
 * @section KYVPLibTL_IFGetDeviceInfo KYVPLibTL_IFGetDeviceInfo()
 * Inquires information about a device.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_IFGetDeviceInfo(KYVPLibTL_IFGetDeviceInfo_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_IFGetDeviceInfo_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFGetDeviceInfo(KYVPLibTL_IFGetDeviceInfo_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Interface
 * @section KYVPLibTL_IFOpenDevice KYVPLibTL_IFOpenDevice()
 * Opens the given sDeviceID with the given iOpenFlags.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_IFOpenDevice(KYVPLibTL_IFOpenDevice_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_IFOpenDevice_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFOpenDevice(KYVPLibTL_IFOpenDevice_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Interface
 * @section KYVPLibTL_IFRegisterEvent KYVPLibTL_IFRegisterEvent()
 * Register event for PCI Interface.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_IFRegisterEvent(KYVPLibTL_IFRegisterEvent_Args* _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_IFRegisterEvent_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFRegisterEvent(KYVPLibTL_IFRegisterEvent_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Interface
 * @section KYVPLibTL_IFUnregisterEvent KYVPLibTL_IFUnregisterEvent()
 * Unregister event from PCI Interface.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_IFUnregisterEvent(KYVPLibTL_IFUnregisterEvent_Args* _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_IFUnregisterEvent_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFUnregisterEvent(KYVPLibTL_IFUnregisterEvent_Args* _pArgs);


/**
 * @section KYVPLibTL_DevGetPort KYVPLibTL_DevGetPort()
 * Retrieves the port handle for the associated remote device.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DevGetPort(KYVPLibTL_DevGetPort_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DevGetPort_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevGetPort(KYVPLibTL_DevGetPort_Args* _pArgs);

/**
 * @section KYVPLibTL_DevGetNumDataStreams KYVPLibTL_DevGetNumDataStreams()
 * Queries the number of available data streams on this Device module.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DevGetNumDataStreams(KYVPLibTL_DevGetNumDataStreams_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DevGetNumDataStreams_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevGetNumDataStreams(KYVPLibTL_DevGetNumDataStreams_Args* _pArgs);

/**
 * @section KYVPLibTL_DevGetDataStreamID KYVPLibTL_DevGetDataStreamID()
 * Queries the unique ID of the data stream at iIndex in the internal data stream list. 
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DevGetDataStreamID(KYVPLibTL_DevGetDataStreamID_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DevGetDataStreamID_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevGetDataStreamID(KYVPLibTL_DevGetDataStreamID_Args* _pArgs);

/**
 * @section KYVPLibTL_DevOpenDataStream KYVPLibTL_DevOpenDataStream()
 * Opens the given sDataStreamID on the given hDevice. 
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DevOpenDataStream(KYVPLibTL_DevOpenDataStream_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DevOpenDataStream_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevOpenDataStream(KYVPLibTL_DevOpenDataStream_Args* _pArgs);

/**
 * @section KYVPLibTL_DevGetInfo KYVPLibTL_DevGetInfo()
 * Inquires information about a device.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DevGetInfo(KYVPLibTL_DevGetInfo_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DevGetInfo_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevGetInfo(KYVPLibTL_DevGetInfo_Args* _pArgs);

/**
 * @section KYVPLibTL_DevClose KYVPLibTL_DevClose()
 * Closes the Device module associated with the given hDevice handle.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DevClose(KYVPLibTL_DevClose_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DevClose_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevClose(KYVPLibTL_DevClose_Args* _pArgs);

/// @}
/**
 * @defgroup StreamModule Stream module
 * @{
 */
 /**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSAnnounceBuffer KYVPLibTL_DSAnnounceBuffer()
 * This announces a GenTL Consumer allocated memory to the Data Stream associated with the
   hDataStream handle and returns a hBuffer handle which references that single buffer until the
   buffer is revoked.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSAnnounceBuffer(KYVPLibTL_DSAnnounceBuffer_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSAnnounceBuffer_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSAnnounceBuffer(KYVPLibTL_DSAnnounceBuffer_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSAllocAndAnnounceBuffer KYVPLibTL_DSAllocAndAnnounceBuffer()
 * This function allocates the memory for a single buffer and announces this buffer to the Data
    Stream associated with the hDataStream handle and returns a hBuffer handle which
    references that single buffer until the buffer is revoked.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSAllocAndAnnounceBuffer(KYVPLibTL_DSAllocAndAnnounceBuffer_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSAllocAndAnnounceBuffer_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSAllocAndAnnounceBuffer(KYVPLibTL_DSAllocAndAnnounceBuffer_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSStartAcquisition KYVPLibTL_DSStartAcquisition()
 * Starts the acquisition engine on the host. After calling need to send command to 
 *  remote device for start Acquisition
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSStartAcquisition(KYVPLibTL_DSStartAcquisition_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSStartAcquisition_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSStartAcquisition(KYVPLibTL_DSStartAcquisition_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSStopAcquisition KYVPLibTL_DSStopAcquisition()
 * Stops the acquisition engine on the host. After calling need to send command to 
 *  remote device for stop Acquisition
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSStopAcquisition(KYVPLibTL_DSStopAcquisition_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSStopAcquisition_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSStopAcquisition(KYVPLibTL_DSStopAcquisition_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSGetInfo KYVPLibTL_DSGetInfo()
 * Inquires information about the Data Stream module associated with hDataStream.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSGetInfo(KYVPLibTL_DSGetInfo_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSGetInfo_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSGetInfo(KYVPLibTL_DSGetInfo_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSGetBufferID KYVPLibTL_DSGetBufferID()
 * Queries the buffer handle for a given index iIndex.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSGetBufferID(KYVPLibTL_DSGetBufferID_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSGetBufferID_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSGetBufferID(KYVPLibTL_DSGetBufferID_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSClose KYVPLibTL_DSClose()
 * Closes the Data Stream module associated with the given hDataStream handle.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSClose(KYVPLibTL_DSClose_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSClose_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSClose(KYVPLibTL_DSClose_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSRevokeBuffer KYVPLibTL_DSRevokeBuffer()
 * Removes an announced buffer from the acquisition engine.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSRevokeBuffer(KYVPLibTL_DSRevokeBuffer_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSRevokeBuffer_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSRevokeBuffer(KYVPLibTL_DSRevokeBuffer_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSQueueBuffer KYVPLibTL_DSQueueBuffer()
 * This function queues a particular buffer for acquisition.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSQueueBuffer(KYVPLibTL_DSQueueBuffer_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSQueueBuffer_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSQueueBuffer(KYVPLibTL_DSQueueBuffer_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSGetBufferInfo KYVPLibTL_DSGetBufferInfo()
 * Inquire information about the Buffer module associated with hBuffer on the hDataStream
    instance.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSGetBufferInfo(KYVPLibTL_DSGetBufferInfo_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSGetBufferInfo_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSGetBufferInfo(KYVPLibTL_DSGetBufferInfo_Args* _pArgs);

/// @}
/**
 * @defgroup EventInterface Event Interface
 * @{
 */
/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSRegisterEvent KYVPLibTL_DSRegisterEvent()
 * Registers an event object to a certain iEventID. The implementation might change depending
     on the platform. 
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSRegisterEvent(KYVPLibTL_DSRegisterEvent_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSRegisterEvent_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSRegisterEvent(KYVPLibTL_DSRegisterEvent_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Stream
 * @section KYVPLibTL_DSUnregisterEvent KYVPLibTL_DSUnregisterEvent()
 * A call to this function will unregister the given iEventID from the given hModule.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_DSUnregisterEvent(KYVPLibTL_DSUnregisterEvent_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_DSUnregisterEvent_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSUnregisterEvent(KYVPLibTL_DSUnregisterEvent_Args* _pArgs);


/**
 * @page KYVPLibTL_Functions_Event
 * @section KYVPLibTL_EventGetData KYVPLibTL_EventGetData()
 * Retrieves the next event data entry from the event data queue associated with the hEvent.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_EventGetData(KYVPLibTL_EventGetData_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_EventGetData_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_EventGetData(KYVPLibTL_EventGetData_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Event
 * @section KYVPLibTL_EventGetDataInfo KYVPLibTL_EventGetDataInfo()
 * Retrieves the next event data entry from the event data queue associated with the hEvent.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_EventGetDataInfo(KYVPLibTL_EventGetDataInfo_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_EventGetDataInfo_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_EventGetDataInfo(KYVPLibTL_EventGetDataInfo_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Event
 * @section KYVPLibTL_EventGetInfo KYVPLibTL_EventGetInfo()
 * Retrieves information about the given hEvent object.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_EventGetInfo(KYVPLibTL_EventGetInfo_ Struct _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_EventGetInfo_ Struct
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_EventGetInfo(KYVPLibTL_EventGetInfo_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Event
 * @section KYVPLibTL_EventFlush KYVPLibTL_EventFlush()
 * Flushes all events in the given hEvent object.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_EventFlush(KYVPLibTL_EventFlush_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_EventFlush_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_EventFlush(KYVPLibTL_EventFlush_Args* _pArgs);

/**
 * @page KYVPLibTL_Functions_Event
 * @section KYVPLibTL_EventKill KYVPLibTL_EventKill()
 * Terminates a waiting operation on a previously registered event object.
 * @subsection C C
 * @code{.c} KY_RESULT KYVPLibTL_EventKill(KYVPLibTL_EventKill_Args _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYVPLibTL_EventKill_Args
 * @return KY_RESULT Status and error report
 */
KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_EventKill(KYVPLibTL_EventKill_Args* _pArgs);

/**
 * @}
 */


//////////////////////////////////////////////////////////////////////
/// VPLIBTL API FUNCTIONS (VERSION 1)
//////////////////////////////////////////////////////////////////////

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_GetPortURL_V1(KYVP_TL_HANDLE _hTLHandle, char* _pszURL, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_ReadPort_V1(KYVP_TL_HANDLE _hTLHandle, uint64_t _iAddress, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_WritePort_V1(KYVP_TL_HANDLE _hTLHandle, uint64_t _iAddress, const void* _pBuffer, size_t* _piSize);


KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_PCIInterface_GetPortURL_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, char* _pszURL, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_PCIInterface_ReadPort_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, uint64_t _iAddress, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_PCIInterface_WritePort_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, uint64_t _iAddress, const void* _pBuffer, size_t* _piSize);


KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Device_GetPortURL_V1(KYVP_DEVICE_HANDLE _hDevHandle, char* _pszURL, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Device_ReadPort_V1(KYVP_DEVICE_HANDLE _hDevHandle, uint64_t _iAddress, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Device_WritePort_V1(KYVP_DEVICE_HANDLE _hDevHandle, uint64_t _iAddress, const void* _pBuffer, size_t* _piSize);


KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_RemoteDevice_GetPortURL_V1(KYVP_REMOTE_DEVICE_HANDLE _hRemoteDeviceHandle, char* _pszURL, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_RemoteDevice_ReadPort_V1(KYVP_REMOTE_DEVICE_HANDLE _hRemoteDeviceHandle, uint64_t _iAddress, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_RemoteDevice_WritePort_V1(KYVP_REMOTE_DEVICE_HANDLE _hRemoteDeviceHandle, uint64_t _iAddress, const void* _pBuffer, size_t* _piSize);


KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Stream_GetPortURL_V1(KYVP_STREAM_HANDLE _hStreamHandle, char* _pszURL, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Stream_ReadPort_V1(KYVP_STREAM_HANDLE _hStreamHandle, uint64_t _iAddress, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_Stream_WritePort_V1(KYVP_STREAM_HANDLE _hStreamHandle, uint64_t _iAddress, const void* _pBuffer, size_t* _piSize);


KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLOpen_V1(KYVP_TL_HANDLE* _phTLHandle);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLClose_V1(KYVP_TL_HANDLE _hTLHandle);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLGetInfo_V1(KYVP_TL_HANDLE _hTLHandle, KYVP_TL_INFO_CMD _eInfoCmd, KYVP_INFO_DATATYPE* _pType, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLGetNumInterfaces_V1(KYVP_TL_HANDLE _hTLHandle, uint32_t* _piNumIfaces);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLGetInterfaceID_V1(KYVP_TL_HANDLE _hTLHandle, uint32_t _iIndex, char* _pszID, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLGetInterfaceInfo_V1(KYVP_TL_HANDLE _hTLHandle, const char* _pszIfaceID, KYVP_INTERFACE_INFO_CMD _eInfoCmd, KYVP_INFO_DATATYPE* _pType, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLOpenInterface_V1(KYVP_TL_HANDLE _hTLHandle, const char* _pszIfaceID, KYVP_PCI_INTERFACE_HANDLE* _phPCIInterfaceHandle);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_TLUpdateInterfaceList_V1(KYVP_TL_HANDLE _hTLHandle, KY_BOOL* _pbChanged, uint64_t _iTimeout);


KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFClose_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFGetInfo_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, KYVP_INTERFACE_INFO_CMD _eInfoCmd, KYVP_INFO_DATATYPE* _pType, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFGetNumDevices_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, uint32_t* _pNumDevices);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFGetDeviceID_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, uint32_t _iIndex, char* _pszID, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFUpdateDeviceList_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, KY_BOOL* _pbChanged, uint64_t _uTimeout);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFGetDeviceInfo_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, const char* _pszIfaceID, KYVP_DEVICE_INFO_CMD _eInfoCmd, KYVP_INFO_DATATYPE* _pType, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFOpenDevice_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, const char* _pszDeviceID, KYVP_DEVICE_ACCESS_FLAGS _iOpenFlags, KYVP_DEVICE_HANDLE* _phDeviceHandle);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFRegisterEvent_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, KYVP_EVENT_TYPE _iEventID, KYVP_EVENT_HANDLE* _phEvent);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_IFUnregisterEvent_V1(KYVP_PCI_INTERFACE_HANDLE _hIFHandle, KYVP_EVENT_TYPE _iEventID);


KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevGetPort_V1(KYVP_DEVICE_HANDLE _hDevHandle, KYVP_REMOTE_DEVICE_HANDLE* _phRemoteDeviceHandle);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevGetNumDataStreams_V1(KYVP_DEVICE_HANDLE _hDevHandle, uint32_t* _piNumDataStreams);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevGetDataStreamID_V1(KYVP_DEVICE_HANDLE _hDevHandle, uint32_t _iIndex, char* _sDataStreamID, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevOpenDataStream_V1(KYVP_DEVICE_HANDLE _hDevHandle, const char* _pszDataStreamID, KYVP_STREAM_HANDLE* _phDataStream);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevGetInfo_V1(KYVP_DEVICE_HANDLE _hDevHandle, KYVP_DEVICE_INFO_CMD _iInfoCmd, KYVP_INFO_DATATYPE* _piType, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DevClose_V1(KYVP_DEVICE_HANDLE _hDevHandle);


KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSAnnounceBuffer_V1(KYVP_STREAM_HANDLE _hDSHandle, void* _pBuffer, size_t _iSize, void* _pPrivate, KYVP_BUFFER_HANDLE* _phBuffer);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSAllocAndAnnounceBuffer_V1(KYVP_STREAM_HANDLE _hDSHandle, size_t _iSize, void* _pPrivate, KYVP_BUFFER_HANDLE* _phBuffer);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSStartAcquisition_V1(KYVP_STREAM_HANDLE _hDSHandle, KYVP_ACQ_START_FLAGS _iStartFlags, uint64_t _iNumToAcquire);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSStopAcquisition_V1(KYVP_STREAM_HANDLE _hDSHandle, KYVP_ACQ_STOP_FLAGS _iStopFlags);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSGetInfo_V1(KYVP_STREAM_HANDLE _hDSHandle, KYVP_STREAM_INFO_CMD _eInfoCmd, KYVP_INFO_DATATYPE* _pType, void* _pBuffer, size_t* _piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSGetBufferID_V1(KYVP_STREAM_HANDLE _hDSHandle, uint32_t _iIndex, KYVP_BUFFER_HANDLE* _ppBufferHandle);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSClose_V1(KYVP_STREAM_HANDLE _hDSHandle);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSRevokeBuffer_V1(KYVP_STREAM_HANDLE _hDSHandle, KYVP_BUFFER_HANDLE _hBufferHandle, void** _ppBuffer, void** _ppPrivate);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSQueueBuffer_V1(KYVP_STREAM_HANDLE _hDSHandle, KYVP_BUFFER_HANDLE _hBufferHandle);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSGetBufferInfo_V1(KYVP_STREAM_HANDLE _hDSHandle, KYVP_BUFFER_HANDLE _hBufferHandle, KYVP_BUFFER_INFO_CMD _iInfoCmd, KYVP_INFO_DATATYPE* _piType, void* _pBuffer, size_t* _piSize);


KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSRegisterEvent_V1(KYVP_STREAM_HANDLE _hDSHandle, KYVP_EVENT_TYPE _iEventID, KYVP_EVENT_HANDLE* _phEvent);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_DSUnregisterEvent_V1(KYVP_STREAM_HANDLE _hDSHandle, KYVP_EVENT_TYPE _iEventID);


KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_EventGetData_V1(KYVP_EVENT_HANDLE _hEventHandle, void* _pBuffer, size_t* _piSize, uint64_t _iTimeout);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_EventGetDataInfo_V1(KYVP_EVENT_HANDLE _hEventHandle, const void* pInBuffer, size_t _iInSize, KYVP_EVENT_DATA_INFO_CMD _iInfoCmd, void* _pOutBuffer, size_t* _piOutSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_EventGetInfo_V1(KYVP_EVENT_HANDLE _hEventHandle, KYVP_EVENT_INFO_CMD _iInfoCmd, KYVP_INFO_DATATYPE* _piType, void* _pBuffer, size_t* piSize);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_EventFlush_V1(KYVP_EVENT_HANDLE _hEventHandle);

KYVPTL_API KY_RESULT KYVP_CALLCONV KYVPLibTL_EventKill_V1(KYVP_EVENT_HANDLE _hEventHandle);


//////////////////////////////////////////////////////////////////////
/// VPLIBTL API FUNCTIONS HANDLE COMPARISON
//////////////////////////////////////////////////////////////////////

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_TLHandleIsInvalid(KYVP_TL_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_TLHandleIsNull(KYVP_TL_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_TLHandlesIsEqual(KYVP_TL_HANDLE _hHandleFirst, KYVP_TL_HANDLE _hHandleSecond);


KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_IFHandleIsInvalid(KYVP_PCI_INTERFACE_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_IFHandleIsNull(KYVP_PCI_INTERFACE_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_IFHandlesAreEqual(KYVP_PCI_INTERFACE_HANDLE _hHandleFirst, KYVP_PCI_INTERFACE_HANDLE _hHandleSecond);


KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_DevHandleIsInvalid(KYVP_DEVICE_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_DevHandleIsNull(KYVP_DEVICE_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_DevHandlesIsEqual(KYVP_DEVICE_HANDLE _hHandleFirst, KYVP_DEVICE_HANDLE _hHandleSecond);


KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_RemoteDeviceHandleIsInvalid(KYVP_REMOTE_DEVICE_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_RemoteDeviceHandleIsNull(KYVP_REMOTE_DEVICE_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_RemoteDeviceHandlesIsEqual(KYVP_REMOTE_DEVICE_HANDLE _hHandleFirst, KYVP_REMOTE_DEVICE_HANDLE _hHandleSecond);


KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_DSHandleIsInvalid(KYVP_STREAM_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_DSHandleIsNull(KYVP_STREAM_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_DSHandlesIsEqual(KYVP_STREAM_HANDLE _hHandleFirst, KYVP_STREAM_HANDLE _hHandleSecond);


KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_BufferHandleIsInvalid(KYVP_BUFFER_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_BufferHandleIsNull(KYVP_BUFFER_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_BufferHandlesIsEqual(KYVP_BUFFER_HANDLE _hHandleFirst, KYVP_BUFFER_HANDLE _hHandleSecond);


KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_EventHandleIsInvalid(KYVP_EVENT_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_EventHandleIsNull(KYVP_EVENT_HANDLE _hHandle);

KYVPTL_API KY_BOOL KYVP_CALLCONV KYVPLibTL_EventHandlesIsEqual(KYVP_EVENT_HANDLE _hHandleFirst, KYVP_EVENT_HANDLE _hHandleSecond);


#endif /* KYVP_LIB_TL_H_ */


