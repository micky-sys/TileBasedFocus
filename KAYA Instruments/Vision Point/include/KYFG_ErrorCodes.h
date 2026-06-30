#ifndef KYFG_ERROR_CODES_H_
#define KYFG_ERROR_CODES_H_

/**
 * \page Enumerations Enumerations
 * \brief
 * \section FGSTATUS FGSTATUS
 * Execution of system error and status. Defines the status returned after each function execution. While some error statuses are general some point to a specific error
 * | Enumeration Field  							| Value | Description |
 * |-------------									|--------|			-----------------------				|
 * | FGSTATUS_OK             						| 0x3000 |  The operation has successfully executed. 	|
 * | FGSTATUS_UNKNOWN_HANDLE 						| 0x3001 |   Unknown API handle                      	|
 * | FGSTATUS_HW_NOT_FOUND	 						| 0x3002 |Error with hardware. The hardware function failed to execute.|
 * | FGSTATUS_BUSY 									| 0x3003 |Can’t execute the function at the current moment, the FG is busy.|
 * | FGSTATUS_FILE_NOT_FOUND						| 0x3004 | Wasn’t able to open the file in the given file path.|
 * | FGSTATUS_FILE_READ_ERROR						| 0x3005 | Wasn’t able to read the file, error in the file or the file is too long.|
 * | FGSTATUS_CONFIG_NOT_LOADED						| 0x3006 | Can’t load current camera configuration.|
 * | FGSTATUS_INVALID_VALUE							| 0x3007 | CThe value given as a parameter is out of the acceptable range.|
 * | FGSTATUS_MAX_CONNECTIONS						| 0x3008 | No more devices can be connected to the system.|
 * | FGSTATUS_MEMORY_ERROR							| 0x3009 | General memory error or an allocation has failed.|
 * | FGSTATUS_WRONG_PARAMETER_NAME					| 0x300A | Parameter name wasn’t found (names are defined by XML file).|
 * | FGSTATUS_WRONG_PARAMETER_TYPE					| 0x300B | Unsupported parameter type.|
 * | FGSTATUS_GENICAM_EXCEPTION						| 0x300C | General Gen<i>Cam exception.|
 * | FGSTATUS_OUT_OF_RANGE_ADDRESS					| 0x300D | The specified address is not suitable for writing.|
 * | FGSTATUS_COULD_NOT_START						| 0x300E | FG couldn’t start the acquisition.|
 * | FGSTATUS_COULD_NOT_STOP						| 0x300F | FG couldn’t stop the acquisition.|
 * | FGSTATUS_XML_FILE_NOT_LOADED					| 0x3010 | No valid XML file source was found.|
 * | FGSTATUS_INVALID_VALUES_FILE					| 0x3011 |Unsupported values file was loaded.|
 * | FGSTATUS_NO_REQUIRED_PARAMETERS_SECTION		| 0x3012 |Corrupted values save file.|
 * | FGSTATUS_WRONG_PARAMETERS_SECTION				| 0x3013 |Saved values configurations for loading weren’t found.|
 * | FGSTATUS_VALUE_HAS_NO_SELECTOR					| 0x3014 |The parameter is not a part of a selector type field.|
 * | FGSTATUS_CALLBACK_NOT_ASSIGNED					| 0x3015 |No callback is assigned for data retrieval.|
 * | FGSTATUS_HANDLE_DOES_NOT_MATCH_CONFIG			| 0x3016 |The value of the Camera Selector doesn't match the provided Camera handle. This will indicate that a wrong CAMHANDLE is introduced for set/get Grabber parameter functions, which contradictory the “CameraSelector” currently set.|																
 * | FGSTATUS_BUFFER_TOO_SMALL						| 0x3017 | Provided buffer length is too small to hold the amount of information needed to be filled in the provided buffer. |
 * | FGSTATUS_BUFFER_UNSUPPORTED_SIZE 				| 0x3018 | Provided buffer size is unsupported and unable to hold the amount of information needed to be filled in the provided buffer |
 * | FGSTATUS_GRABBER_FIRMWARE_NOT_SUPPORTED		| 0x3019 | This indicates that no authentication HW controller exists. |
 * | FGSTATUS_PARAMETER_NOT_WRITABLE				| 0x301A | Unable to set value for the provided parameter. |
 * | FGSTATUS_CANNOT_START_HW_STREAM				| 0x301B | Unable to start camera acquisition. |
 * | FGSTATUS_WRONG_SCHEMA_VERSION					| 0x301C | Wrong or missed required camera configuration value. |
 * | FGSTATUS_CAMERA_OR_GRABBER_SECTION_NOT_ARRAY	| 0x301D | JSON file section type mismatch. |
 * | FGSTATUS_ROOT_IS_NOT_OBJECT 					| 0x301E | Unable to Get next (grabber or camera) parameter from an array. |
 * | FGSTATUS_NO_PARAMETER_TYPE 					| 0x301F | Parameter type is not supported. |
 * | FGSTATUS_FILE_CREATE_ERROR 					| 0x3020 | Unable to create file with the provided file path. |
 * | FGSTATUS_COULD_NOT_STOP_STREAM 				| 0x3021 | Unable to sto camera acquisition. |
 * | FGSTATUS_BUFFER_MEMORY_OVERLAP 				| 0x3022 | Buffer memory ovelap with previously announced buffer. |
 * | FGSTATUS_UNSUPPORTED_PARAMETER_TYPE 			| 0x3023 | Provided data type is unsupported. |
 * | FGSTATUS_OPERATION_TIMEOUT 					| 0x3024 | Command timeout error on specified channel. |
 * | FGSTATUS_OPERATION_BLOCKED 					| 0x3025 | Operation blocked on specified channel. |
 * | FGSTATUS_PARAMETER_NOT_READABLE 				| 0x3026 | An attempt to call a currently unreadable parameter will result in this error. |
 * | FGSTATUS_PARAMETER_NO_CONTEXT 					| 0x3027 | Provided parameter has no context. |
 * | FGSTATUS_EXCEEDED_MAX_CAMERA_CONNECTIONS 		| 0x3100 | The number of connected cameras exceeds the maximum allowed connected cameras. |
 * | FGSTATUS_QUEUED_BUFFERS_NOT_SUPPORTED 			| 0x3101 | Queued buffer does not supported by the hardware. |
 * | FGSTATUS_DESTINATION_QUEUE_NOT_SUPPORTED 		| 0x3102 | Feature is not yet implemented Move buffers to this queue are reserved for future library enhancements. |
 * | FGSTATUS_INVALID_STREAM_INFO_CMD 				| 0x3103 | Requested stream information is invalid |
 * | FGSTATUS_INVALID_STREAM_BUFFER_INFO_CMD		| 0x3104 | Requested information about previously announced buffer is invalid |
 * | FGSTATUS_STREAM_NOT_CREATED					| 0x3105 | Reserved |
 * | FGSTATUS_GRABBER_NOT_CONNECTED					| 0x3106 | Specified hardware is not connected |
 * | FGSTATUS_CAMERA_NOT_CONNECTED					| 0x3107 | Specified camera is not connected |
 * | FGSTATUS_GRABBER_NOT_OPENED					| 0x3108 | Specified hardware is not open |
 * | FGSTATUS_CAMERA_NOT_OPENED						| 0x3109 | Specified camera is not open |
 * | FGSTATUS_BUFFER_ALREADY_IN_INPUT_QUEUE			| 0x310A | Specified buffer is already been moved to queue by the application |
 * | FGSTATUS_STREAM_CANNOT_LOCK					| 0x310B | Stream is started and cannot be re-started |
 * | FGSTATUS_STREAM_IS_LOCKED						| 0x310C | Stream is being used and cannot be deleted |
 * | FGSTATUS_CAMERA_NODES_NOT_INITIALIZED			| 0x3200 | Reserved |
 * | FGSTATUS_UPDATE_WRONG_VID						| 0x3300 | Retrieved information about firmware contained in supplied binary file is wrong(vendor IDs) |
 * | FGSTATUS_UPDATE_WRONG_BOARD_ID					| 0x3301 | Retrieved information about firmware contained in supplied binary file is wrong (board ID). |
 * | FGSTATUS_CANNOT_WRITE_IMAGE					| 0x3400 | Unable to save the specified image file. |
 * | FGSTATUS_FACILITY_DISABLED						| 0x3FFD | Camera stopped when trying to detect retaining running camera. Unable to release previous stream. |
 * | FGSTATUS_FEATURE_NOT_IMPLEMENTED				| 0x3FFE | Unable to change parameter specified by the user if it is not implemented or if the user should not  be able to change it. |
 * | FGSTATUS_UNKNOWN_ERROR							| 0x3FFF | Unknown error. |
 
 
 * \section CSSTATUS CSSTATUS
 * Execution of system error and status for Chameleon simulator. Defines the status returned after each function execution. While some error statuses are general some point to a specific error.
 * | Enumeration Field  							| Value 	| Description |
 * |-------------									|-----------|			-----------------------				|
 * | CSSTATUS_OK									| 0x2000 | The operation has successfully executed. |
 * | CSSTATUS_UNKNOWN_SIM_HANDLE					| 0x2001 | Unknown API handle. |
 * | CSSTATUS_HW_NOT_FOUND							| 0x2002 | Error with hardware. The hardware function failed to execute. |
 * | CSSTATUS_BUSY									| 0x2003 | Can’t execute the function at the current moment, the simulator is busy. |
 * | CSSTATUS_FILE_NOT_FOUND						| 0x2004 | Wasn’t able to open file in given file path. |
 * | CSSTATUS_FILE_READ_ERROR						| 0x2005 | Wasn’t able to read the file, error in the file or the file is too long. |
 * | CSSTATUS_CONFIG_NOT_LOADED						| 0x2006 | Can’t load current camera configuration. |
 * | CSSTATUS_INVALID_VALUE							| 0x2007 | The value given as a parameter is out of the acceptable range. |
 * | CSSTATUS_MAX_CONNECTIONS						| 0x2008 | No more devices can be connected to system. |
 * | CSSTATUS_COULD_NOT_STOP						| 0x2009 | The simulation couldn’t stop. |
 * | CSSTATUS_CANNOT_LOAD_IMAGE_FILE				| 0x200A | Wasn’t able to load image file. |
 * | CSSTATUS_MEMORY_ERROR							| 0x200B | General memory error or an allocation has failed. |
 * | CSSTATUS_UNKNOWN_SIM_CONTROL					| 0x200C | Wrong simulation command. |
 * | CSSTATUS_WRONG_PARAMETER_NAME					| 0x200D | Camera configuration wasn’t found. |
 * | CSSTATUS_WRONG_PARAMETER_TYPE					| 0x200E | Wrong parameter type for camera configuration. |
 * | CSSTATUS_GENICAM_EXCEPTION						| 0x200F | General Gen<i>Cam exception. |
 * | CSSTATUS_OUT_OF_RANGE_ADDRESS					| 0x2010 | The specified address is not suitable for writing. |
 * | CSSTATUS_PATH_INVALID							| 0x2011 | The specified file couldn’t be opened, wrong file path or file not found. |
 * | CSSTATUS_FILE_TYPE_INVALID						| 0x2012 | Invalid file type was entered. |
 * | CSSTATUS_UNSUPPORTED_IMAGE						| 0x2013 | Mounted image is not supported. |
 * | CSSTATUS_UNSUPPORTED_IMAGE_CONVERSION			| 0x2014 | Couldn’t convert image type or couldn’t rescale current image. |
 * | CSSTATUS_UNSUPPORTED_DEPTH_CONVERSION			| 0x2015 | Couldn’t convert the image due to unsupported color depth. |
 * | CSSTATUS_INVALID_VALUES_FILE					| 0x2016 | Invalid camera configuration values file was loaded. |
 * | CSSTATUS_FILE_WRITE_ERROR						| 0x2017 | Wasn’t able to save the camera configuration values file. |
 * | CSSTATUS_BUFFER_NOT_LOADED						| 0x2018 | An incompatible buffer was selected, or no buffer was allocated. |
 * | CSSTATUS_TRIGGER_NOT_SET						| 0x2019 | Unable to start triggered generation, was not set. |
 * | CSSTATUS_CANNOT_SET_USER_REGISTER_ADDRESS		| 0x201A | Unable to set user’s select start address. |
 * | CSSTATUS_CANNOT_READ_USER_REGISTER				| 0x201B | Unable to read user’s data chunks (in bytes). |
 * | CSSTATUS_CANNOT_WRITE_USER_REGISTER			| 0x201C | Unable to write user’s data chunks (in bytes). |
 * | CSSTATUS_CANNOT_WRITE_REGISTER					| 0x201D | Unable to write to the requested register. |
 * | CSSTATUS_IMAGE_HEADER_INJECTION_SIZE_TOO_BIG	| 0x201E | Requested image header size is too big. |
 * | CSSTATUS_NO_EXTENDED_HW_FEATURES				| 0x201F | Unable to read user’s register address. |
 * | CSSTATUS_MAX_USER_ADDRESS_EXCEEDED				| 0x2020 | Unable to write user’s register address. |
 * | CSSTATUS_UNKNOWN_ERROR							| 0x2FFF | Unknown error. |
*/

typedef enum _fgstatus
{
    CSSTATUS_OK                                 = 0x2000, 
    CSSTATUS_UNKNOWN_SIM_HANDLE                 = 0x2001, 
    CSSTATUS_HW_NOT_FOUND                       = 0x2002,
    CSSTATUS_BUSY                               = 0x2003,
    CSSTATUS_FILE_NOT_FOUND                     = 0x2004,
    CSSTATUS_FILE_READ_ERROR                    = 0x2005,
    CSSTATUS_CONFIG_NOT_LOADED                  = 0x2006,
    CSSTATUS_INVALID_VALUE                      = 0x2007,
    CSSTATUS_MAX_CONNECTIONS                    = 0x2008,
    CSSTATUS_COULD_NOT_STOP                     = 0x2009,
    CSSTATUS_CANNOT_LOAD_IMAGE_FILE             = 0x200A,
    CSSTATUS_MEMORY_ERROR                       = 0x200B,
    CSSTATUS_UNKNOWN_SIM_CONTROL                = 0x200C,
    CSSTATUS_WRONG_PARAMETER_NAME               = 0x200D,
    CSSTATUS_WRONG_PARAMETER_TYPE               = 0x200E,
    CSSTATUS_GENICAM_EXCEPTION                  = 0x200F,
    CSSTATUS_OUT_OF_RANGE_ADDRESS               = 0x2010,
    CSSTATUS_PATH_INVALID                       = 0x2011,
    CSSTATUS_FILE_TYPE_INVALID                  = 0x2012,
    CSSTATUS_UNSUPPORTED_IMAGE                  = 0x2013,
    CSSTATUS_UNSUPPORTED_IMAGE_CONVERSION       = 0x2014,
    CSSTATUS_UNSUPPORTED_DEPTH_CONVERSION       = 0x2015,
    CSSTATUS_INVALID_VALUES_FILE                = 0x2016,
    CSSTATUS_FILE_WRITE_ERROR                   = 0x2017,
    CSSTATUS_BUFFER_NOT_LOADED                  = 0x2018,
    CSSTATUS_TRIGGER_NOT_SET                    = 0x2019,
    CSSTATUS_CANNOT_SET_USER_REGISTER_ADDRESS   = 0x201A,
    CSSTATUS_CANNOT_READ_USER_REGISTER          = 0x201B,
    CSSTATUS_CANNOT_WRITE_USER_REGISTER         = 0x201C,
    CSSTATUS_CANNOT_READ_REGISTER               = 0x201D,
    CSSTATUS_CANNOT_WRITE_REGISTER              = 0x201E,
    CSSTATUS_IMAGE_HEADER_INJECTION_SIZE_TOO_BIG = 0x201F,
    CSSTATUS_NO_EXTENDED_HW_FEATURES            = 0x2020,
    CSSTATUS_MAX_USER_ADDRESS_EXCEEDED          = 0x2021,


    FGSTATUS_OK                                 = 0x3000, 
    FGSTATUS_UNKNOWN_HANDLE                     = 0x3001, 
    FGSTATUS_HW_NOT_FOUND                       = 0x3002,
    FGSTATUS_BUSY                               = 0x3003,
    FGSTATUS_FILE_NOT_FOUND                     = 0x3004,
    FGSTATUS_FILE_READ_ERROR                    = 0x3005,
    FGSTATUS_CONFIG_NOT_LOADED                  = 0x3006,
    FGSTATUS_INVALID_VALUE                      = 0x3007,
    FGSTATUS_MAX_CONNECTIONS                    = 0x3008,
    FGSTATUS_MEMORY_ERROR                       = 0x3009,
    FGSTATUS_WRONG_PARAMETER_NAME               = 0x300A,
    FGSTATUS_WRONG_PARAMETER_TYPE               = 0x300B,
    FGSTATUS_GENICAM_EXCEPTION                  = 0x300C,
    FGSTATUS_OUT_OF_RANGE_ADDRESS               = 0x300D,
    FGSTATUS_COULD_NOT_START                    = 0x300E,
    FGSTATUS_COULD_NOT_STOP                     = 0x300F,
    FGSTATUS_XML_FILE_NOT_LOADED                = 0x3010,
    FGSTATUS_INVALID_VALUES_FILE                = 0x3011,
    FGSTATUS_NO_REQUIRED_PARAMETERS_SECTION     = 0x3012,
    FGSTATUS_WRONG_PARAMETERS_SECTION           = 0x3013,
    FGSTATUS_VALUE_HAS_NO_SELECTOR              = 0x3014,
    FGSTATUS_CALLBACK_NOT_ASSIGNED              = 0x3015,
    FGSTATUS_HANDLE_DOES_NOT_MATCH_CONFIG       = 0x3016,
    FGSTATUS_BUFFER_TOO_SMALL                   = 0x3017,
    FGSTATUS_BUFFER_UNSUPPORTED_SIZE            = 0x3018,
    FGSTATUS_GRABBER_FIRMWARE_NOT_SUPPORTED     = 0x3019,
    FGSTATUS_PARAMETER_NOT_WRITABLE             = 0x301A,
    FGSTATUS_CANNOT_START_HW_STREAM             = 0x301B,
    FGSTATUS_WRONG_SCHEMA_VERSION               = 0x301C,
    FGSTATUS_CAMERA_OR_GRABBER_SECTION_NOT_ARRAY= 0x301D,
    FGSTATUS_ROOT_IS_NOT_OBJECT                 = 0x301E,
    FGSTATUS_NO_PARAMETER_TYPE                  = 0x301F,
    FGSTATUS_FILE_CREATE_ERROR                  = 0x3020,
    FGSTATUS_COULD_NOT_STOP_STREAM              = 0x3021,
    FGSTATUS_BUFFER_MEMORY_OVERLAP              = 0x3022,
    FGSTATUS_UNSUPPORTED_PARAMETER_TYPE         = 0x3023,
    FGSTATUS_OPERATION_TIMEOUT                  = 0x3024,
    FGSTATUS_OPERATION_BLOCKED                  = 0x3025,
    FGSTATUS_PARAMETER_NOT_READABLE             = 0x3026,
    FGSTATUS_PARAMETER_NO_CONTEXT               = 0x3027,

    FGSTATUS_EXCEEDED_MAX_CAMERA_CONNECTIONS    = 0x3100,

    FGSTATUS_QUEUED_BUFFERS_NOT_SUPPORTED       = 0x3101,
    FGSTATUS_DESTINATION_QUEUE_NOT_SUPPORTED    = 0x3102,
    FGSTATUS_INVALID_STREAM_INFO_CMD            = 0x3103,
    FGSTATUS_INVALID_STREAM_BUFFER_INFO_CMD     = 0x3104,
    FGSTATUS_STREAM_NOT_CREATED                 = 0x3105,

    FGSTATUS_GRABBER_NOT_CONNECTED              = 0x3106,
    FGSTATUS_CAMERA_NOT_CONNECTED               = 0x3107,

    FGSTATUS_GRABBER_NOT_OPENED                 = 0x3108,
    FGSTATUS_CAMERA_NOT_OPENED                  = 0x3109,

    FGSTATUS_BUFFER_ALREADY_IN_INPUT_QUEUE      = 0x310A,
    FGSTATUS_STREAM_CANNOT_LOCK                 = 0x310B,  // a Stream is started and cannot be re-started
    FGSTATUS_STREAM_IS_LOCKED                   = 0x310C,  // a Stream is being used and cannot be deleted 

    FGSTATUS_FRAMESTART_NOT_SUPPORTED           = 0x310E,  // KYFG_STREAMBUFFER_CALLBACK_FRAMESTART flag not supported by this grabber

    FGSTATUS_CAMERA_NODES_NOT_INITIALIZED       = 0x3200,
    FGSTATUS_FAILED_LOCKING_CAMERA_LINKS        = 0x3201,  // KYFG_CameraOpen2 or KYFG_LoadFirmware couldn't lock all camera's links to the process
    FGSTATUS_FAILED_LOCKING_DEVICE              = 0x3202,  // KYFG_CameraScanEx couldn't device to the process

    FGSTATUS_UPDATE_WRONG_VID                   = 0x3300,
    FGSTATUS_UPDATE_WRONG_BOARD_ID              = 0x3301,

    FGSTATUS_CANNOT_WRITE_IMAGE                 = 0x3400,
    FGSTATUS_CANNOT_READ_IMAGE                  = 0x3401,

    FGSTATUS_CONCURRENT_API_CALL                = 0x3500,  // concurrent API call detected

    FGSTATUS_FACILITY_DISABLED                  = 0x3FFD,
    FGSTATUS_FEATURE_NOT_IMPLEMENTED            = 0x3FFE,
    FGSTATUS_UNKNOWN_ERROR                      = 0x3FFF,

}FGSTATUS;

#endif  // #ifndef KYFG_ERROR_CODES_H_
