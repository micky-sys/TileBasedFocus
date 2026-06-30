#ifndef KYFOUNDATION_ERRORS_H_
#define KYFOUNDATION_ERRORS_H_

//////////////////////////////////////////////////////////////////////////////////
///  LIST OF COMMON RESULT CODES:
//////////////////////////////////////////////////////////////////////////////////

#define KY_RESULT_CODE_GENERIC_SUCCESS                                      0x0
#define KY_RESULT_CODE_GENERIC_ERROR                                        0x1

#define KY_RESULT_CODE_INTERFACE_NOT_SET                                    0x2
#define KY_RESULT_CODE_BAD_CONTEXT                                          0x3
#define KY_RESULT_CODE_MEMORY_ERROR                                         0x4
#define KY_RESULT_CODE_INVALID_CONFIG                                       0x5
#define KY_RESULT_CODE_INITIALIZATION_FAILED                                0x6
#define KY_RESULT_CODE_INVALID_PARAMETER                                    0x7
#define KY_RESULT_CODE_INVALID_CRC_CALCULATION                              0x8
#define KY_RESULT_CODE_CANNOT_LOCK_PROCESS_RESOURCE                         0x9
#define KY_RESULT_CODE_FILE_NOT_FOUND                                       0xA
#define KY_RESULT_CODE_FILE_CANNOT_OPEN                                     0xB
#define KY_RESULT_CODE_FILE_READ_ERROR                                      0xC
#define KY_RESULT_CODE_FILE_WRITE_ERROR                                     0xD
#define KY_RESULT_CODE_OPERATION_BLOCKED                                    0xE
#define KY_RESULT_CODE_BUFFER_TOO_SMALL                                     0xF
#define KY_RESULT_CODE_FUNCTIONALITY_NOT_IMPLEMENTED                        0x10
#define KY_RESULT_CODE_TIMEOUT                                              0x11
#define KY_RESULT_CODE_INVALID_INDEX                                        0x12
#define KY_RESULT_CODE_INVALID_ID                                           0x13
#define KY_RESULT_CODE_ABORT                                                0x14
#define KY_RESULT_CODE_INVALID_HANDLE                                       0x15
#define KY_RESULT_CODE_RESOURCE_IN_USE                                      0x16
#define KY_RESULT_CODE_INVALID_JSON                                         0x17
#define KY_RESULT_CODE_JSON_PROPERTY_NOT_FOUND                              0x18
#define KY_RESULT_CODE_TARGET_IS_READONLY                                   0x19
#define KY_RESULT_CODE_STREAM_NOT_READY_FOR_START                           0x1A

//////////////////////////////////////////////////////////////////////////////////
/// LIST OF COMMON RESULT CODES SEVERITIES:
//////////////////////////////////////////////////////////////////////////////////

#define KY_SEVERITY_FATAL                                                   0x0
#define KY_SEVERITY_ALERT                                                   0x1
#define KY_SEVERITY_CRITICAL                                                0x2
#define KY_SEVERITY_ERROR                                                   0x3
#define KY_SEVERITY_WARNING                                                 0x4
#define KY_SEVERITY_NOTICE                                                  0x5
#define KY_SEVERITY_INFO                                                    0x6
#define KY_SEVERITY_DEBUG                                                   0x7

//////////////////////////////////////////////////////////////////////////////////
///  KY_RESULT STRUCT
//////////////////////////////////////////////////////////////////////////////////

#pragma pack(push, 1)
typedef struct _KY_RESULT
{
    uint32_t uResult;
} KY_RESULT;
#pragma pack(pop)

#endif /* KYFOUNDATION_ERRORS_H_ */
