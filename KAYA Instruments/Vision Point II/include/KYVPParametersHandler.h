#pragma once

#include "KYVPDefines.h"
#include "KYVPNodeDescriptor.h"

#ifdef _MSC_VER
#ifdef KYVPPARAMLIB_EXPORTS
#define KYVPPARAMLIB_EXTERNAL_DLL __declspec(dllexport)
#else
#define KYVPPARAMLIB_EXTERNAL_DLL __declspec(dllimport)
#endif
#else
#define KYVPPARAMLIB_EXTERNAL_DLL
#endif

#define KYVPPARAMLIB_API KYVP_EXTERNAL_C KYVPPARAMLIB_EXTERNAL_DLL
#define KYVPPARAMLIB_VAR KYVP_EXTERNAL KYVPPARAMLIB_EXTERNAL_DLL

#define KYVP_MAX_ERROR_STRING_BUFFER_SIZE 1024

/**
 * @page ParametersHandlerLibrary Parameters Handler Library
 * @subpage KYVP_ParametersHandler_library_description <br>
 * @subpage KYVPParametersHandlerStructs <br>
 * @subpage KYVP_NodeDescriptor <br>
 * @subpage KYParametersHandler_Functions
 */

/**
 * @page KYVP_ParametersHandler_library_description KYVP_ParametersHandler Description
 * @includedoc KYVP_ParametersHandler_library.html
 */

/**
 * @page Library_Exiting Library Exiting
 * @includedoc Library_Exiting.html
 */

/**
 * @page KYParametersHandler_Functions Parameters Handler Functions
 * @ref KYParametersHandler_InitFunctions <br>
 * @ref KYParametersHandler_UninitFunctions <br>
 * @ref KYParametersHandler_SetGetFunctions <br>
 * @ref KYParametersHandler_ComparisonFunctions <br>
 */
/// //////////////////////////////////////////////////////////////////////
/// KYVP_COLLECTION_HANDLE DEFINES
//////////////////////////////////////////////////////////////////////

#define COLLECTION_INVALID_HANDLE_INIT_VALUE ((void*)-1)
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYVP_COLLECTION_HANDLE KYVP_COLLECTION_HANDLE
 * | Struct Field                                               | Description                       | 
 * | -----------------------                                    | -----------                       | 
 * | pNativePointer                                             | Pointer to Collection Handle      | 
 */
#if defined(_MSC_VER)
#  pragma warning(push)
#  pragma warning(disable:4514) /* unreferenced inline function has been removed */
#endif
#pragma pack(push, 1)
typedef struct _KYVP_COLLECTION_HANDLE
{
#ifdef __cplusplus
    _KYVP_COLLECTION_HANDLE()
        : pNativePointer(COLLECTION_INVALID_HANDLE_INIT_VALUE)
    {
    }

    constexpr 
        void* operator()() const { return pNativePointer; } // DO NOT USE!!! Will be removed in future versions

protected:
    _KYVP_COLLECTION_HANDLE(void* _pNativePointer)
        : pNativePointer(_pNativePointer)
    {
    }
#endif
    void* pNativePointer;

} KYVP_COLLECTION_HANDLE;
#pragma pack(pop)
#if defined(_MSC_VER)
#  pragma warning(pop)
#endif

/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section CALLBACK_FUNCTIONS CALLBACK FUNCTIONS
 * | Callback function                                          | Definition                        |
 * | -----------------------                                    | -----------                       |
 * | TRANSPORT_FUNC                                             | @code{.c} typedef void (*TRANSPORT_FUNC)(KYParametersHandler_TransportCallParams_Args* _pArgs); @endcode      |
 * | PARAMETER_CALLBACK_FUNC                                    | @code{.c} typedef void (*PARAMETER_CALLBACK_FUNC)(const KYVP_NodeDescriptor* _pNodeDescriptor, void* _pContext); @endcode      |
 */
KYVPPARAMLIB_VAR const KYVP_COLLECTION_HANDLE KYVP_COLLECTION_HANDLE_NULL;
KYVPPARAMLIB_VAR const KYVP_COLLECTION_HANDLE KYVP_COLLECTION_HANDLE_INVALID;

#define COLLECTION_HANDLE_IS_INVALID(_hHandle)                      KYParametersHandler_CollectionHandleIsInvalid(_hHandle)
#define COLLECTION_HANDLE_IS_NULL(_hHandle)                         KYParametersHandler_CollectionHandleIsNull(_hHandle)
#define COLLECTION_HANDLES_IS_EQUAL(_hHandleFirst, _hHandleSecond)  KYParametersHandler_CollectionHandlesIsEqual(_hHandleFirst, _hHandleSecond)

//////////////////////////////////////////////////////////////////////
/// KYPARAMETERSHANDLER TRANSPORT
//////////////////////////////////////////////////////////////////////

/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_TransportOperationType ENUM KYParametersHandler_TransportOperationType 
 * | Enum Field                                                 | Value     | Description       | Remarks   |
 * | -----------------------                                    | --------  | -----------       | -----     |
 * | KYParametersHandler_TransportOperationType_Read            | 0x0       | Read Action       |           |
 * | KYParametersHandler_TransportOperationType_Write           | 0x1       | Write Action      |           |
 * | KYParametersHandler_TransportOperationType_GetResourceData | 0x2       | Get Resource Data |           |
 */
typedef enum _KYParametersHandler_TransportOperationType
{
    KYParametersHandler_TransportOperationType_Read,
    KYParametersHandler_TransportOperationType_Write,
    KYParametersHandler_TransportOperationType_GetResourceData

}KYParametersHandler_TransportOperationType;

typedef enum _KYParametersHandler_TransportOperationResult
{
    KYParametersHandler_TransportOperationResult_Success,
    KYParametersHandler_TransportOperationResult_Failed,
    KYParametersHandler_TransportOperationResult_Timeout,

}KYParametersHandler_TransportOperationResult;

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_TRANSPORTCALLPARAMS_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_TRANSPORTCALLPARAMS_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_TransportCallParams_Args KYParametersHandler_TransportCallParams_Args
 * | Structure Field                                | Type                                              | Description                                           | Remarks   |
 * | -------------                                  | -----                                             | -----------                                           | -------   |
 * | version                                        | uint32_t                                          | structure version                                     |           |
 * | eOperation                                     | @ref KYParametersHandler_TransportOperationType   | Operation type: Read, Write or GetResourceData file   |           |
 * | pData                                          | void*                                             | Pointer to current operation buffer                   |           |
 * | pContext                                       | void*                                             | Pointer to context                                    |           |
 * | iAddress                                       | uint64_t                                          | Access address                                        |           |
 * | iSize                                          | size_t                                            | ResourceData file size in bytes                       |           |
 * | bResourceDataZipped                            | KY_BOOL                                           | Is ResourceData file zipped                           |           |
 * | eResult                                        | @ref KYParametersHandler_TransportOperationResult | Transport operation finished result                   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_TransportCallParams_Args
{
    uint32_t                                        version                 KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_TRANSPORTCALLPARAMS_STRUCT_VERSION);
    KYParametersHandler_TransportOperationType      eOperation              KYVP_MEMBER_DEFAULT(KYParametersHandler_TransportOperationType_Read); // Operation type: Read, Write or GetResourceData file
    void*                                           pData                   KYVP_MEMBER_DEFAULT(nullptr); // Pointer to current operation buffer
    void*                                           pContext                KYVP_MEMBER_DEFAULT(nullptr); // Pointer to context
    uint64_t                                        iAddress                KYVP_MEMBER_DEFAULT(0); // Access address
    size_t                                          iSize                   KYVP_MEMBER_DEFAULT(0); // ResourceData file size in bytes
    KY_BOOL                                         bResourceDataZipped     KYVP_MEMBER_DEFAULT(KY_FALSE); // Is ResourceData file zipped
    KYParametersHandler_TransportOperationResult    eResult                 KYVP_MEMBER_DEFAULT(KYParametersHandler_TransportOperationResult_Failed); // Transport operation finished result

}KYParametersHandler_TransportCallParams_Args;
#pragma pack(pop)

typedef void (*TRANSPORT_FUNC)(KYParametersHandler_TransportCallParams_Args* _pArgs);

//////////////////////////////////////////////////////////////////////
/// ARGUMENT STRUCTURES
//////////////////////////////////////////////////////////////////////

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_INITPARAMETERS_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_INITPARAMETERS_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_InitParameters KYParametersHandler_InitParameters
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | uint32_t                                       | version                                       | Version of this structure definition, must be between 1 and KY_MAX_INIT_VERSION                   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_InitParameters
{
    uint32_t                                version            KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_INITPARAMETERS_STRUCT_VERSION); // Version of this structure definition, must be between 1 and KY_MAX_INIT_VERSION

}KYParametersHandler_InitParameters;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_INITIALIZE_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_INITIALIZE_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_Initialize_Args KYParametersHandler_Initialize_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | struct version                                        |           |
 * | pInitParameters                                | @ref KYParametersHandler_InitParameters       | pointer to KYParametersHandler_InitParameters struct  |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_Initialize_Args
{
    uint32_t                                version             KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_INITIALIZE_STRUCT_VERSION);
    KYParametersHandler_InitParameters*     pInitParameters     KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString      KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize   KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_Initialize_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_CREATEPARAMETERCOLLECTION_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_CREATEPARAMETERCOLLECTION_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_CreateParameterCollection_Args KYParametersHandler_CreateParameterCollection_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | phCollectionHandle                             | @ref KYVP_COLLECTION_HANDLE KYVP_COLLECTION_HANDLE*         | Pointer to KYVP_COLLECTION_HANDLE that will be filled |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_CreateParameterCollection_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_CREATEPARAMETERCOLLECTION_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE*                 phCollectionHandle      KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString          KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize       KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_CreateParameterCollection_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_DELETEPARAMETERCOLLECTION_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_DELETEPARAMETERCOLLECTION_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_DeleteParameterCollection_Args KYParametersHandler_DeleteParameterCollection_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_DeleteParameterCollection_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_DELETEPARAMETERCOLLECTION_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle       KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    char*                                   pszErrorString          KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize       KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_DeleteParameterCollection_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_REGISTERPARAMETERCOLLECTIONTRANSPORT_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_REGISTERPARAMETERCOLLECTIONTRANSPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_RegisterParameterCollectionTransport_Args KYParametersHandler_RegisterParameterCollectionTransport_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pTRANSPORT_FUNC                                | @ref CALLBACK_FUNCTIONS TRANSPORT_FUNC        | Pointer to user transport function                    |           |
 * | pContext                                       | void*                                         | Pointer to User context                               |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_RegisterParameterCollectionTransport_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_REGISTERPARAMETERCOLLECTIONTRANSPORT_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle       KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    TRANSPORT_FUNC                          pTRANSPORT_FUNC         KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pContext                KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString          KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize       KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_RegisterParameterCollectionTransport_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_UNREGISTERPARAMETERCOLLECTIONTRANSPORT_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_UNREGISTERPARAMETERCOLLECTIONTRANSPORT_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_UnregisterParameterCollectionTransport_Args KYParametersHandler_UnregisterParameterCollectionTransport_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_UnregisterParameterCollectionTransport_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_UNREGISTERPARAMETERCOLLECTIONTRANSPORT_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle       KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    char*                                   pszErrorString          KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize       KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_UnregisterParameterCollectionTransport_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_REGISTERPARAMETERCOLLECTIONCALLBACK_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_REGISTERPARAMETERCOLLECTIONCALLBACK_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_RegisterParameterCollectionCallback_Args KYParametersHandler_RegisterParameterCollectionCallback_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pPARAMETER_CALLBACK_FUNC                       | @ref CALLBACK_FUNCTIONS PARAMETER_CALLBACK_FUNC | Pointer to user Callback function                   |           |
 * | pContext                                       | void*                                         | Pointer to userContext                                |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_RegisterParameterCollectionCallback_Args
{
    uint32_t                                version                             KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_REGISTERPARAMETERCOLLECTIONCALLBACK_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle                   KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    PARAMETER_CALLBACK_FUNC                 pPARAMETER_CALLBACK_FUNC            KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pContext                            KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString                      KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize                   KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_RegisterParameterCollectionCallback_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_UNREGISTERPARAMETERCOLLECTIONCALLBACK_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_UNREGISTERPARAMETERCOLLECTIONCALLBACK_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_UnregisterParameterCollectionCallback_Args KYParametersHandler_UnregisterParameterCollectionCallback_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_UnregisterParameterCollectionCallback_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_UNREGISTERPARAMETERCOLLECTIONCALLBACK_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle       KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    char*                                   pszErrorString          KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize       KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_UnregisterParameterCollectionCallback_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_INITIALIZEPARAMETERCOLLECTION_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_INITIALIZEPARAMETERCOLLECTION_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_InitializeParameterCollection_Args KYParametersHandler_InitializeParameterCollection_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_InitializeParameterCollection_Args
{
    uint32_t                                version                 KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_INITIALIZEPARAMETERCOLLECTION_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle       KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    char*                                   pszErrorString          KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize       KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_InitializeParameterCollection_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_GETNODEDESCRIPTOR_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_GETNODEDESCRIPTOR_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_GetNodeDescriptor_Args KYParametersHandler_GetNodeDescriptor_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszParamName                                   | const char*                                   | Parameter name                                        |           |
 * | ppNodeDescriptor                               | @ref KYVP_NodeDescriptor**                    | Pointer to Node Descriptor                            |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_GetNodeDescriptor_Args
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_GETNODEDESCRIPTOR_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    const char*                             pszParamName                    KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_NodeDescriptor**                   ppNodeDescriptor                KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_GetNodeDescriptor_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_GETNODEDESCRIPTORSARRAY_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_GETNODEDESCRIPTORSARRAY_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_GetNodeDescriptorsArray_Args KYParametersHandler_GetNodeDescriptorsArray_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | ppArray                                        | @ref const KYVP_NodeDescriptor**              | Pointer to const Node Descriptors Array. The pointed data must be treated as read-only: modifying its contents is undefined behavior |           |
 * | piSize                                         | size_t*                                       | Pointer to size_t number of elements in array         |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_GetNodeDescriptorsArray_Args
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_GETNODEDESCRIPTORSARRAY_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    const KYVP_NodeDescriptor**             ppArray                         KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piSize                          KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_GetNodeDescriptorsArray_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_GETVALUE_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_GETVALUE_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_GetValue_Args KYParametersHandler_GetValue_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszParamName                                   | const char*                                   | Parameter name                                        |           |
 * | pParamValue                                    | void*                                         | Parameter value                                       |           |
 * | piParamValueSize                               | size_t*                                       | Param value size in bytes                             |           |
 * | pszErrorString                                 | char*                                         | pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | pointer to size_t variable with buffer size provided   |           |
*/
#pragma pack(push, 1)
typedef struct _KYParametersHandler_GetValue_Args
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_GETVALUE_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    const char*                             pszParamName                    KYVP_MEMBER_DEFAULT(nullptr);
    void*                                   pParamValue                     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piParamValueSize                KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_GetValue_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_SETVALUE_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_SETVALUE_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_SetValue_Args KYParametersHandler_SetValue_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszParamName                                   | const char*                                   | Param name                                            |           |
 * | pParamValue                                    | const void*                                   | Param value                                           |           |
 * | piParamValueSize                               | size_t*                                       | Param value size in bytes                             |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_SetValue_Args
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_SETVALUE_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    const char*                             pszParamName                    KYVP_MEMBER_DEFAULT(nullptr);
    const void*                             pParamValue                     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piParamValueSize                KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_SetValue_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_GETVALUEENUMASSTRING_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_GETVALUEENUMASSTRING_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_GetValueEnumAsString_Args KYParametersHandler_GetValueEnumAsString_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszEnumerationName                             | const char*                                   | Enumeration param name                                |           |
 * | pszParamValueString                            | char*                                         | Buffer for current param name                         |           |
 * | piParamValueStringSize                         | size_t*                                       | Buffer value size in bytes                            |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided  |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_GetValueEnumAsString_Args
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_GETVALUEENUMASSTRING_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    const char*                             pszEnumerationName              KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszParamValueString             KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piParamValueStringSize          KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_GetValueEnumAsString_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_SETVALUEENUMBYVALUENAME_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_SETVALUEENUMBYVALUENAME_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_SetValueEnumByValueName_Args KYParametersHandler_SetValueEnumByValueName_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszParamName                                   | const char*                                   | Param name                                            |           |
 * | pszParamValue                                  | const char*                                   | Param value                                           |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_SetValueEnumByValueName_Args
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_SETVALUE_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    const char*                             pszParamName                    KYVP_MEMBER_DEFAULT(nullptr);
    const char*                             pszParamValue                   KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_SetValueEnumByValueName_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_INVALIDATEPARAMETERCOLLECTION_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_INVALIDATEPARAMETERCOLLECTION_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_InvalidateParameterCollection_Args KYParametersHandler_InvalidateParameterCollection_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_InvalidateParameterCollection_Args
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_INVALIDATEPARAMETERCOLLECTION_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_InvalidateParameterCollection_Args;
#pragma pack(pop)

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_INVALIDATEPARAMETER_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_INVALIDATEPARAMETER_STRUCT_VERSION 1
#endif
/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_InvalidateParameter_Args KYParametersHandler_InvalidateParameter_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszParamName                                   | const char*                                   | Parameter name                                        |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_InvalidateParameter_Args
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_INVALIDATEPARAMETER_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    const char*                             pszParamName                    KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_InvalidateParameter_Args;
#pragma pack(pop)

/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_SaveToFile_Args KYParametersHandler_SaveToFile_Args
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszFilePath                                    | const char*                                   | Path to file                                          |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */

#ifdef __cplusplus
static const int KYPARAMETERSHANDLER_SAVETOFILE_STRUCT_VERSION = 1;
#else
#define KYPARAMETERSHANDLER_SAVETOFILE_STRUCT_VERSION 1
#endif

#pragma pack(push, 1)
typedef struct _KYParametersHandler_SaveToFile_Args
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_SAVETOFILE_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    const char*                             pszFilePath                     KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piFilePathSize                  KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);

}KYParametersHandler_SaveToFile_Args;
#pragma pack(pop)

/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_GetParameterAttributeValueArgs KYParametersHandler_GetParameterAttributeValueArgs
 * | Structure Field                                | Type                                          | Description                                           | Remarks   |
 * | -------------                                  | -----                                         | -----------                                           | -------   |
 * | version                                        | uint32_t                                      | Struct version                                        |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                     |           |
 * | pszParamName                                   | const char*                                   | Parameter name                                        |           |
 * | eAttributeType                                 | KYVP_ParameterAttributeType                   | Attribute type                                        |           |
 * | pszPropertyValue                               | char*                                         | Pointer toValue of attribute that should be filled    |           |
 * | stringSize                                     | size_t*                                       | Pointer value string size that should be filled       |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided   |           |
 */
#pragma pack(push, 1)
typedef struct _KYParametersHandler_GetParameterAttributeValueArgs
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_SAVETOFILE_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    const char*                             pszParamName                    KYVP_MEMBER_DEFAULT(nullptr);
    KYVP_ParameterAttributeType             eAttributeType                  KYVP_MEMBER_DEFAULT(KYVP_ParameterAttributeType_Invalidator);
    char*                                   pszPropertyValue                KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piPropertyValueSize             KYVP_MEMBER_DEFAULT(nullptr);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);
}KYParametersHandler_GetParameterAttributeValueArgs;
#pragma pack(pop)

/**
 * @page KYVPParametersHandlerStructs Parameters Handler Library Structs
 * @section KYParametersHandler_IsParameterAvailableArgs KYParametersHandler_IsParameterAvailableArgs
 * | Structure Field                                | Type                                          | Description                                                   | Remarks   |
 * | -------------                                  | -----                                         | -----------                                                   | -------   |
 * | version                                        | uint32_t                                      | Struct version                                                |           |
 * | hCollectionHandle                              | @ref KYVP_COLLECTION_HANDLE                   | Collection handle                                             |           |
 * | pszParamName                                   | const char*                                   | Parameter name                                                |           |
 * | bIsAvailable                                   | KY_BOOL                                       | KY_TRUE if the parameter is available in the collection       |           |
 * | pszErrorString                                 | char*                                         | Pointer to buffer for returning an error description. If an error occurs, the error message will be copied to the specified buffer. If the provided pointer is nullptr, the error string will not be copied. |           |
 * | piErrorStringSize                              | size_t*                                       | Pointer to size_t variable with buffer size provided          |           |
 */
    #pragma pack(push, 1)
    typedef struct _KYParametersHandler_IsParameterAvailableArgs
{
    uint32_t                                version                         KYVP_MEMBER_DEFAULT(KYPARAMETERSHANDLER_SAVETOFILE_STRUCT_VERSION);
    KYVP_COLLECTION_HANDLE                  hCollectionHandle               KYVP_MEMBER_DEFAULT(KYVP_COLLECTION_HANDLE_INVALID);
    const char*                             pszParamName                    KYVP_MEMBER_DEFAULT(nullptr);
    KY_BOOL                                 bIsAvailable                    KYVP_MEMBER_DEFAULT(KY_FALSE);
    char*                                   pszErrorString                  KYVP_MEMBER_DEFAULT(nullptr);
    size_t*                                 piErrorStringSize               KYVP_MEMBER_DEFAULT(nullptr);
}KYParametersHandler_IsParameterAvailableArgs;
#pragma pack(pop)

//////////////////////////////////////////////////////////////////////
/// KYPARAMETERSHANDLER API FUNCTIONS
//////////////////////////////////////////////////////////////////////

/// @defgroup KYParametersHandler_InitFunctions Init functions
/// @{
/**
 * @section KYParametersHandler_Initialize KYParametersHandler_Initialize()
 * @brief Initializes KYParametersHandler library.
 * @subsection C C
 * @code{.c} KYParametersHandler_Initialize(KYParametersHandler_Initialize_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_Initialize_Args KYParametersHandler_Initialize_Args 
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_Initialize(KYParametersHandler_Initialize_Args* _pArgs);
/**
 * @section KYParametersHandler_CreateParameterCollection KYParametersHandler_CreateParameterCollection()
 * @brief Create instance of ParameterCollection
 * @subsection C C
 * @code{.c} KYParametersHandler_CreateParameterCollection(KYParametersHandler_CreateParameterCollection_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_CreateParameterCollection_Args KYParametersHandler_CreateParameterCollection_Args 
 * @return KY_RESULT Status and error report

 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_CreateParameterCollection(KYParametersHandler_CreateParameterCollection_Args* _pArgs);
/**
 * @section KYParametersHandler_RegisterParameterCollectionTransport KYParametersHandler_RegisterParameterCollectionTransport()
 * @brief Register TRANSPORT_FUNC to parameter collection for perform operations
 * @subsection C C
 * @code{.c} KYParametersHandler_RegisterParameterCollectionTransport(KYParametersHandler_RegisterParameterCollectionTransport_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_RegisterParameterCollectionTransport_Args KYParametersHandler_RegisterParameterCollectionTransport_Args 
 * @return KY_RESULT Status and error report

 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_RegisterParameterCollectionTransport(KYParametersHandler_RegisterParameterCollectionTransport_Args* _pArgs);
/**
 * @section KYParametersHandler_RegisterParameterCollectionCallback KYParametersHandler_RegisterParameterCollectionCallback()
 * @brief Register PARAMETER_CALLBACK_FUNC to parameter collection for updating about parameter changes.
 * @subsection C C
 * @code{.c} KYParametersHandler_RegisterParameterCollectionCallback(KYParametersHandler_RegisterParameterCollectionCallback_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_RegisterParameterCollectionCallback_Args
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_RegisterParameterCollectionCallback(KYParametersHandler_RegisterParameterCollectionCallback_Args* _pArgs);
/**
 * @section KYParametersHandler_InitializeParameterCollection KYParametersHandler_InitializeParameterCollection()
 * @brief Initialize parameter collection. Mandatory to call before using collection.
 * @subsection C C
 * @code{.c} KYParametersHandler_InitializeParameterCollection(KYParametersHandler_InitializeParameterCollection_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_InitializeParameterCollection_Args
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_InitializeParameterCollection(KYParametersHandler_InitializeParameterCollection_Args* _pArgs);
/// @}

/// @defgroup KYParametersHandler_UninitFunctions Uninit functions
/// @{
/**
 * @section KYParametersHandler_DeleteParameterCollection KYParametersHandler_DeleteParameterCollection()
 * @brief Delete instance of ParameterCollection and release all resources.
 * @subsection C C
 * @code{.c} KYParametersHandler_DeleteParameterCollection(KYParametersHandler_DeleteParameterCollection_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pioner to @ref KYParametersHandler_DeleteParameterCollection_Args 
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_DeleteParameterCollection(KYParametersHandler_DeleteParameterCollection_Args* _pArgs);
/**
 * @section KYParametersHandler_UnregisterParameterCollectionTransport KYParametersHandler_UnregisterParameterCollectionTransport()
 * @brief Unregister TRANSPORT_FUNC in parameter collection
 * @subsection C C
 * @code{.c}  KYParametersHandler_UnregisterParameterCollectionTransport(KYParametersHandler_UnregisterParameterCollectionTransport_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_UnregisterParameterCollectionTransport_Args 
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_UnregisterParameterCollectionTransport(KYParametersHandler_UnregisterParameterCollectionTransport_Args* _pArgs);
/**
 * @section KYParametersHandler_UnregisterParameterCollectionCallback KYParametersHandler_UnregisterParameterCollectionCallback()
 * @brief Unregister PARAMETER_CALLBACK_FUNC in parameter collection.
 * @subsection C C
 * @code{.c} KYParametersHandler_UnregisterParameterCollectionCallback(KYParametersHandler_UnregisterParameterCollectionCallback_Args* _pArgs) @endcode
 * @param [in,out] _pArgs poiner to @ref KYParametersHandler_UnregisterParameterCollectionCallback_Args 
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_UnregisterParameterCollectionCallback(KYParametersHandler_UnregisterParameterCollectionCallback_Args* _pArgs);
/// @}

/// @defgroup KYParametersHandler_SetGetFunctions Set and Get functions
/// @{
/**
 * @section KYParametersHandler_GetNodeDescriptor KYParametersHandler_GetNodeDescriptor()
 * @brief Get requested KY_NodeDescriptor for parameter collection by param name.
 * @subsection C C
 * @code{.c} KYParametersHandler_GetNodeDescriptor(KYParametersHandler_GetNodeDescriptor_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_GetNodeDescriptor_Args
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_GetNodeDescriptor(KYParametersHandler_GetNodeDescriptor_Args* _pArgs);
/**
 * @section KYParametersHandler_GetNodeDescriptorsArray KYParametersHandler_GetNodeDescriptorsArray()
 * @brief Get array of KY_NodeDescriptors for parameter collection.
 * @subsection C C
 * @code{.c} KYParametersHandler_GetNodeDescriptorsArray(KYParametersHandler_GetNodeDescriptorsArray_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_GetNodeDescriptorsArray_Args
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_GetNodeDescriptorsArray(KYParametersHandler_GetNodeDescriptorsArray_Args* _pArgs);
/**
 * @section KYParametersHandler_GetValue KYParametersHandler_GetValue()
 * @brief Get value of collection parameter
 * @subsection C C
 * @code{.c} KYParametersHandler_GetValue(KYParametersHandler_GetValue_Args* _pArgs); @endcode
 * @param [in,out] _pArgs poiner to @ref KYParametersHandler_GetValue_Args 
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_GetValue(KYParametersHandler_GetValue_Args* _pArgs);
/**
 * @section KYParametersHandler_SetValue KYParametersHandler_SetValue()
 * @brief Set value of collection parameter.
 * @subsection C C
 * @code{.c} KYParametersHandler_SetValue(KYParametersHandler_SetValue_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_SetValue_Args 
 * @return KY_RESULT Status and error report
*/
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_SetValue(KYParametersHandler_SetValue_Args* _pArgs);
/**
 * @section KYParametersHandler_GetValueEnumAsString KYParametersHandler_GetValueEnumAsString()
 * @brief Get enum current value as string.
 * @subsection C C
 * @code{.c} KYParametersHandler_GetValueEnumAsString(KYParametersHandler_GetValueEnumAsString_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_GetValueEnumAsString
 * @return KY_RESULT Status and error report
*/
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_GetValueEnumAsString(KYParametersHandler_GetValueEnumAsString_Args* _pArgs);
/**
 * @section KYParametersHandler_SetValueEnumByValueName KYParametersHandler_SetValueEnumByValueName()
 * @brief Set value of collection parameter.
 * @subsection C C
 * @code{.c} KYParametersHandler_SetValueEnumByValueName(KYParametersHandler_SetValueEnumByValueName_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_SetValueEnumByValueName_Args
 * @return KY_RESULT Status and error report
*/
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_SetValueEnumByValueName(KYParametersHandler_SetValueEnumByValueName_Args* _pArgs);
/**
 * @section KYParametersHandler_InvalidateParameterCollection KYParametersHandler_InvalidateParameterCollection()
 * @brief Invalidate the all collection parameters.
 * @subsection C C
 * @code{.c} KYParametersHandler_InvalidateParameterCollection(KYParametersHandler_InvalidateParameterCollection_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_InvalidateParameterCollection_Args 
 * @return KY_RESULT Status and error report
*/
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_InvalidateParameterCollection(KYParametersHandler_InvalidateParameterCollection_Args* _pArgs);
/**
 * @section KYParametersHandler_InvalidateParameter KYParametersHandler_InvalidateParameter()
 * @brief Invalidate the collection parameter.
 * @subsection C C
 * @code{.c} KYParametersHandler_InvalidateParameter(KYParametersHandler_InvalidateParameter_Args* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_InvalidateParameter_Args
 * @return KY_RESULT Status and error report
*/
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_InvalidateParameter(KYParametersHandler_InvalidateParameter_Args* _pArgs);
/**
 * @section KYParametersHandler_SaveToFile KYParametersHandler_SaveToFile()
 * @brief Save loaded data to file.
 * @subsection C C
 * @code{.c} KYParametersHandler_SaveToFile(KYParametersHandler_SaveToFiles_Struct* _pArgs); @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_SaveToFile_Args 
 * @return KY_RESULT Status and error report
*/
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_SaveToFile(KYParametersHandler_SaveToFile_Args* _pArgs);

/**
 * @section KYParametersHandler_GetParameterAttributeValue KYParametersHandler_GetParameterAttributeValue()
 * @brief Get attribute of parameters
 * @subsection C C
 * @code{.c}  KYParametersHandler_GetParameterAttributeValue(KYParametersHandler_GetParameterAttributeValueArgs* _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_GetParameterAttributeValueArgs
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_GetParameterAttributeValue(KYParametersHandler_GetParameterAttributeValueArgs* _pArgs);

/**
 * @section KYParametersHandler_IsParameterAvailable KYParametersHandler_IsParameterAvailable()
 * @brief Get attribute of parameters
 * @subsection C C
 * @code{.c}  KYParametersHandler_IsParameterAvailable(KYParametersHandler_IsParameterAvailableArgs* _pArgs) @endcode
 * @param [in,out] _pArgs pointer to @ref KYParametersHandler_IsParameterAvailableArgs
 * @return KY_RESULT Status and error report
 */
KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_IsParameterAvailable(KYParametersHandler_IsParameterAvailableArgs* _pArgs);
/// @}

//////////////////////////////////////////////////////////////////////
/// KYPARAMETERSHANDLER API FUNCTIONS (VERSION 1)
//////////////////////////////////////////////////////////////////////

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_Initialize_V1(KYParametersHandler_InitParameters* _pKYParametersHandler_InitParameters,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_CreateParameterCollection_V1(KYVP_COLLECTION_HANDLE* _phCollectionHandle,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_DeleteParameterCollection_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_RegisterParameterCollectionTransport_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    TRANSPORT_FUNC _pTRANSPORT_FUNC,
    void* _pContext,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_UnregisterParameterCollectionTransport_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_RegisterParameterCollectionCallback_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    PARAMETER_CALLBACK_FUNC _pPARAMETER_CALLBACK_FUNC,
    void* _pContext,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_UnregisterParameterCollectionCallback_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_InitializeParameterCollection_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_GetNodeDescriptor_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszParamName,
    KYVP_NodeDescriptor** _ppNodeDescriptor,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_GetNodeDescriptorsArray_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const KYVP_NodeDescriptor** _ppArray,
    size_t* _piSize,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_GetValue_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszParamName,
    void* _pParamValue,
    size_t* _piParamValueSize,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_SetValue_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszParamName,
    const void* _pParamValue,
    size_t* _piParamValueSize,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_GetValueEnumAsString_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszEnumerationName,
    char* _pszParamValueString,
    size_t* _piParamValueStringSize,
    char* _pszErrorString,
    size_t* _piErrorStringSize);

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_SetValueEnumByValueName_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszParamName,
    const char* _pszParamValue,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_SetValueEnum_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszParamName,
    int64_t _Value,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_SetValueInt_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszParamName,
    int64_t _Value,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_SetValueBool_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszParamName,
    KY_BOOL _Value,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_InvalidateParameterCollection_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_InvalidateParameter_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszParamName KYVP_MEMBER_DEFAULT(nullptr),
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_SaveToFile_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszFilePath,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_GetParameterAttributeValue_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* _pszParamName,
    KYVP_ParameterAttributeType _eAttributeType,
    char* _pszPropertyValue,
    size_t* _piPropertyValueSize,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

KYVPPARAMLIB_API KY_RESULT KYVP_CALLCONV KYParametersHandler_IsParameterAvailable_V1(KYVP_COLLECTION_HANDLE _hCollectionHandle,
    const char* pszParamName,
    KY_BOOL* _pbIsAvailable,
    char* _pszErrorString KYVP_MEMBER_DEFAULT(nullptr),
    size_t* _piErrorStringSize KYVP_MEMBER_DEFAULT(nullptr));

//////////////////////////////////////////////////////////////////////
/// KYPARAMETERSHANDLER API FUNCTIONS HANDLE COMPARISON
//////////////////////////////////////////////////////////////////////

/// @defgroup KYParametersHandler_ComparisonFunctions Comparison functions
/// @{
/**
 * @section KYParametersHandler_CollectionHandleIsInvalid KYParametersHandler_CollectionHandleIsInvalid()
 * @brief Check if Collection Handle is Invalid
 * @subsection C C
 * @code{.c} KYParametersHandler_CollectionHandleIsInvalid(KYVP_COLLECTION_HANDLE _hCollectionHandle); @endcode
 * @param [in,out] _hCollectionHandleo KYVP_COLLECTION_HANDLE structure
 * @return KY_BOOL Comparison result
*/
KYVPPARAMLIB_API KY_BOOL KYVP_CALLCONV KYParametersHandler_CollectionHandleIsInvalid(KYVP_COLLECTION_HANDLE _hCollectionHandle);
/**
 * @section KYParametersHandler_CollectionHandleIsNull KYParametersHandler_CollectionHandleIsNull()
 * @brief Check if Collection Handle is NULL
 * @subsection C C
 * @code{.c} KYParametersHandler_CollectionHandleIsNull(KYVP_COLLECTION_HANDLE _hCollectionHandle); @endcode
 * @param [in,out] _hCollectionHandle KYVP_COLLECTION_HANDLE structure
 * @return KY_BOOL Comparison result
*/
KYVPPARAMLIB_API KY_BOOL KYVP_CALLCONV KYParametersHandler_CollectionHandleIsNull(KYVP_COLLECTION_HANDLE _hCollectionHandle);
/**
 * @section KYParametersHandler_CollectionHandlesIsEqual KYParametersHandler_CollectionHandlesIsEqual()
 * @brief Check if Collection Handles is equals
 * @subsection C C
 * @code{.c} KYParametersHandler_CollectionHandlesIsEqual(KYVP_COLLECTION_HANDLE _hCollectionHandleFirst, KYVP_COLLECTION_HANDLE _hCollectionHandleSecond); @endcode
 * @param [in,out] _hCollectionHandleFirst  KYVP_COLLECTION_HANDLE   structure
 * @param [in,out] _hCollectionHandleSecond KYVP_COLLECTION_HANDLE   structure
 * @return KY_BOOL Comparison result
*/
KYVPPARAMLIB_API KY_BOOL KYVP_CALLCONV KYParametersHandler_CollectionHandlesIsEqual(KYVP_COLLECTION_HANDLE _hCollectionHandleFirst, KYVP_COLLECTION_HANDLE _hCollectionHandleSecond);
/// @}


