#ifndef KYFG_LIB_H_
#define KYFG_LIB_H_

#include "KYFGLib_defines.h"
#include "KYFGLibVersion.h"

#ifdef __cplusplus
extern "C" {
#endif
    
/**
 * \page Overview Overview
 * \includedoc Overview.html
 * 
 */
 /**
* \page BuildingAnAPIExample Building an API example
* \includedoc Building_an_API_Example.html
*
*/
/// \defgroup ConnectionAndInfo Connection and info
/// @{
/**
 * \section KYFGLib_Initialize KYFGLib_Initialize()
 * \brief   An optional call before KY_DeviceScan(). Initializes KYFGLib library and fills KYFGLib_InitParameters structure with various parameters about KYFGLib library.
 * \subsection C C
 * \code{.c} FGSTATUS KYFGLib_Initialize(KYFGLib_InitParameters* pKYFGLib_InitParameters); \endcode
 * \param   [out] pKYFGLib_InitParameters   Pointer to KYFGLib_InitParameters structure.
 * \return  Status and error report
 * \subsection Cs C#
 * \code void Lib.Initialize(InitParameters^ initParameters); \endcode
 * \param     [out] initParameters Pointer to InitParameters structure 
 * \subsection Python Python
 * \code{.py} def KYFGLib_Initialize(initParams) \endcode 
 * \param [out] initParams Pointer to InitParameters structure 
 * \return  Status and error report
 * \remarks     1. For details please refer to the KYFGLib_InitParameters structure description.
 * \remarks     2.  Users who want this member to be considered by KYFGLib_Initialize() call must set the version value to 2 or higher.
 

 */
KAYA_API FGSTATUS KYFGLib_Initialize(KYFGLib_InitParameters* pKYFGLib_InitParameters);

// <--- Connection functions --->

/**
 * \section KY_DeviceScan KY_DeviceScan()
 * \brief   Scans for KAYA devices currently connected to the PC PCIe slots and available virtual devices
 * \subsection C C
 * \code{.c} FGSTATUS KY_DeviceScan(int *pDetectedDevices); \endcode
 * \param   [out] pDetectedDevices - Pointer to int that will receive the number of available devices.
 * \return  FGSTATUS - Status and error report.
 * \subsection Example Example code: 
 * \code{.c}
 *         int nDetectedDevices;
 *         KY_DeviceScan(&nDetectedDevices);
 * \endcode
 * \subsection Cs C#
 * \code{.cs} int Lib.Scan(); \endcode
 * \return int Number of connected hardware and virtual devices.
 * \subsection Python Python
 * \code{.py} def KY_DeviceScan() \endcode
 * \return     FGSTATUS - Status and error report. <br>
 *             int - Number of connected hardware and virtual devices. 
 * \remarks The software stack requires “KYService” to be running, otherwise, KY_DeviceScan() will return 0.
 */
KY_API FGSTATUS KY_DeviceScan(int *pDetectedDevices);


/**
 * \section KYFG_Scan KYFG_Scan() (KY_DEPRECATED)
 * @brief   Scan for available devices
 * @param   [out] pids_info - pointer to <pid> array of scanned devices
 *                             null - will retrieve only the number of found devices (when this is used, two function calls needed in order to open the device)
 * @param   [in] count - number of devices assigned to pids_info array (assume pids_info array is valid)
 * @return  int - number of found devices, maximum MAX_INFO
 */
KY_DEPRECATED(KAYA_API int KYFG_Scan(uint32_t *pids_info, int count), "Function 'KYFG_Scan()' is deprecated, please use function 'KY_DeviceScan()'.");


/**
 * \section KY_DeviceInfo KY_DeviceInfo()
 * \brief    Fills KY_DEVICE_INFO structure with info about the relevant device. Before calling this function you must set the 
            field “version” to a value between 0 and the maximum number supported at the time of writing your code. See 
            struct KY_DEVICE_INFO for details.
 * \subsection C C
 * \code{.c}  FGSTATUS KY_DeviceInfo(int index, KY_DEVICE_INFO* pInfo); \endcode
 * \param   [in] index - index of Frame Grabber device
 * \param   [out] pInfo - pointer to struct with device information
 * \return  FGSTATUS - Error code of function execution status
 * \subsection Cs C#
 * \code{.cs} DEVICE_INFO Lib.DeviceInfo(int index); \endcode
 * \param     [in] Discovered device index
 * \return KY_DEVICE_INFO - Returns structure with info about the device.
 * \subsection Python Python
 * \code{.py} def KY_DeviceInfo(index) \endcode
 * \param  [in]  Discovered device index int
 * \return FGSTATUS - Status and error report. <br>KY_DEVICE_INFO - Returns structure with info about the device.

 */
KY_API FGSTATUS KY_DeviceInfo(int index, KY_DEVICE_INFO* pInfo);


/**
 * \brief    Connects to a specific Frame Grabber and initializes all required components.
 * \subsection C C
 * \code{.c} FGHANDLE KYFG_Open(int index); \endcode
 * \param   [in] index     -     The index, from scan result array acquired by the KY_DeviceScan() function, of 
                            the Frame Grabber device to open
 * \return  Returns an API handle to Frame Grabber device. INVALID_FGHANDLE will indicate a wrong, impossible or 
            unsupported connection.
 * \subsection Cs C#
 * \code{.cs} IGrabber Lib.Open(int index); \endcode
 * \param [in] index     The index, from scan result array acquired with KY_DeviceScan() function, of the 
                        Frame Grabber device to open. 
 * \return Instance of Grabber class that implements IGrabber interface
 * \subsection Python Python 
 * \code{.py} def KYFG_Open(deviceIndex) \endcode
 * \param [in] deviceIndex     The index, from the scan result list acquired with the KY_DeviceScan() function, of 
                            the device to open
 * \return     Returns an API handle to Frame Grabber device. INVALID_FGHANDLE will indicate a wrong, impossible or 
            unsupported connection
 * \remarks 1.     The software stack requires “KYService” to be running, otherwise, KYFG_Open() will return INVALID_FGHANDLE
 * \remarks 2.     When calling the function with an index of -1, a connection to the first found Frame Grabber will be established, such function call 
                eliminates the need for KY_DeviceScan() function call.

 */
KAYA_API FGHANDLE     KYFG_Open(int index);


/**
 * \section KYFG_OpenEx KYFG_OpenEx()
 * \brief     Connects to a specific device and initializes all required components with previously saved values. Project file 
            may be passed here in order to initialize Frame Grabber and Camera parameters with previously saved values.
 * \subsection C C
 * \code{.c} FGHANDLE KYFG_OpenEx(int index, const char* projectFile); \endcode
 * \param   [in] index The index, from scan result array, acquired with KY_DeviceScan() function, of the Frame Grabber device to open. 
 * \param   [in] projectFile -(optional) The full path of a project file with saved values. The input value can be NULL.
 * \return  Returns an API handle to the device. INVALID_FGHANDLE will indicate a wrong, impossible or unsupported connection.
 * \subsection Cs C#
 * \code{.cs} IGrabber Lib.OpenEx(int index, System.String projectFile); \endcode
 * Overloads:
 * \code{.cs} IGrabber Lib.OpenEx(int index);\endcode
 * \param [in] index The index, from scan result array acquired with Lib.DeviceScan() function, of the Frame Grabber device to open.
 * \param [in] projectFile (optional) Full path of a project file with saved values. Input value can be NULL. 
 * \return Instance of Grabber class that implements IGrabber interface.
 * \subsection Python Python
 * \code{.py} def KYFG_OpenEx(deviceIndex) \endcode
 * \param [in] deviceIndex The index, from the scan result list acquired with the KY_DeviceScan() function, of the device to open
 * \return Returns an API handle to the device. INVALID_FGHANDLE will indicate a wrong, impossible or unsupported connection.
 * \remarks 1.  A project file can be generated using our Vision Point application. See section "Creating a New Project File" of the 
                “Vision_Point_App_User_Guide” document.

 * \remarks 2.     A project file with previously saved values can be passed in order to initialize camera parameters. For additional information regarding 
                the project file please refer to the Vision Point application user guide: “Vision_Point_App_User_Guide”.
 * \remarks    3.     When calling the function with an index of -1, a connection to the first found Frame Grabber will be established, such function call 
                eliminates the need for KY_DeviceScan() function call

 */
KAYA_API FGHANDLE    KYFG_OpenEx(int index, const char* projectFile);
/**
 * \section KY_DeviceDisplayName KY_DeviceDisplayName() DEPRICATED
 * @brief   This function is deprecated. New applications should use function KY_DeviceInfo() and use 
            pInfo.szDeviceDisplayName to retrieve device name.
            Retrieve the device name for the specified index.

*/
KY_API const char* KY_DeviceDisplayName(int index);

/**
 * \section KYFG_Close KYFG_Close()
 * \brief    Close the device specified by its handle. Stops data acquisition/ generation of all opened cameras, disconnects 
            from all connected cameras and deletes previously created buffers associated with these cameras or camera 
            simulator.
 * \subsection C C
 * \code{.c} FGSTATUS KYFG_Close(FGHANDLE handle); \endcode
 * \param   [in] handle - API handle to chosen Frame Grabber.
 * \return  FGSTATUS -  Status and error report.
 * \subsection Cs C#
 * \code{.cs} void IDevice.Close(); \endcode
 * \subsection Python Python 
 * \code{.py} def KYFG_Close(handle) \endcode
 * \param [in] handle API handle to a chosen device.
 * \return FGSTATUS - Status and error report.

 */
KAYA_API FGSTATUS    KYFG_Close(FGHANDLE handle);
/// @}
#pragma pack(push, 1)
/**
 * \page Structures
 * \section KY_SOFTWARE_VERSION KY_SOFTWARE_VERSION
 *
 * | Structure Field      | Type         | Description | Remarks |
 * | -------------      | -----     | ----------- | ----    
 * | struct_version      | uint16_t    | Must be set to 0…2 before calling KY_GetSoftwareVersion() function | Should be set before calling KY_GetSoftwareVersion() function, (input part) |
 * | Major              | uint16_t    | Software major version | Filled by KY_GetSoftwareVersion() function (output part) |
 * | Minor              | uint16_t    | Software minor version | Filled by KY_GetSoftwareVersion() function (output part) |
 * | SubMinor              | uint16_t    | Software sub minor version | Filled by KY_GetSoftwareVersion() function (output part) |
 * | Beta                  | uint16_t    | Non-zero value indicates a "Beta build" | Filled if “struct” version is 1 or greater |
 * | RC                  | uint16_t    | Non-zero value indicates a "Release Candidate build" | Filled if “struct” version is 1 or greater |
 * | Alpha                 | uint16_t    | Non-zero value indicates a "Alpha build" | Filled if “struct” version is 1 or greater |
*/
typedef struct _KY_SOFTWARE_VERSION
{
    uint16_t struct_version; // Must be set to 0...2 before calling KY_GetSoftwareVersion() function
    uint16_t Major;
    uint16_t Minor;
    uint16_t SubMinor;

    // since version 1 of this structure:
    uint16_t Beta; // Non-zero value indicates a "Beta build"
    uint16_t RC;   // Non-zero value indicates a "Release Candidate build"

    // since version 2 of this structure:
    uint16_t Alpha; // Non-zero value indicates a "Alpha build"
}KY_SOFTWARE_VERSION;
#pragma pack(pop)
/// \defgroup ConnectionAndInfo Connection and info
/// Before starting, please read section 6 on how to perform library exiting correctly.
/// @{
/**
 * \section KY_GetSoftwareVersion KY_GetSoftwareVersion()
 * \brief  Retrieves information about currently running software version.
 * \subsection C C
 * \code{.c} FGSTATUS KYFGLib_KY_GetSoftwareVersion(KY_SOFTWARE_VERSION* pVersion);  \endcode
 * \param   [in, out] pVersion - pointer to struct with versions information
 * \return  Status and error report
 * \subsection Cs C#
 * \code{.cs} KY_SOFTWARE_VERSION Lib.GetSoftwareVersion(); \endcode
 * \return KY_SOFTWARE_VERSION - Structure with info about KAYA Vision Point software.
 * \subsection Python Python 
 * \code{.py} def KY_GetSoftwareVersion() \endcode
 * \return FGSTATUS - Status and error report. <br> KY_SOFTWARE_VERSION - Structure with info about KAYA Vision Point software.
 */
KAYA_API FGSTATUS     KY_GetSoftwareVersion(KY_SOFTWARE_VERSION* pVersion);
/// @}

#define KYFG_AUX_MESSAGE_ID_IO_CONTROLLER 11

#pragma pack(push, 1)
/**
 * \page Structures Structures
 * \section KYFG_IO_AUX_DATA KYFG_IO_AUX_DATA
 * Data portion interpretation of Auxiliary data structure, in case of callback issued by IO controller.
 * | Structure Field      | Type         | Description     |
 * | --------------      | -----        | ---------     |
 * | masked_data          | uint64_t    | Indicates the state of the I/O controller feature that can generate an event according to the table in the remarks.     |
 * | timestamp          | uint64_t    | Event timestamp in units of nano-seconds (nsec)     |
 * \remarks 1. Additional macros are provided to help extract specific IO controller elements:
     * \remarks 1.1 KYFG_IO_CONTROLLER_MASKED_IO – extract mask of GPIO triggers from IO Auxiliary masked data
     * \remarks 1.2. KYFG_IO_CONTROLLER_MASKED_ENCODERS – extract mask of encoders triggers from IO Auxiliary masked data
     * \remarks 1.3. KYFG_IO_CONTROLLER_MASKED_TIMERS – extract mask of timers triggers from IO Auxiliary masked data
     * \remarks 1.4. KYFG_IO_CONTROLLER_MASKED_CAMERA_TRIGGERS – extract mask of camera triggers from IO Auxiliary masked data
     * \remarks 1.5. KYFG_IO_CONTROLLER_MASKED_TRIGGERS – extract mask of stream triggers from IO Auxiliary masked data
 *
 * | Bit| Function                      | Bit     | Function                 | Bit     | Function                 | 
 * | ---| -----------                 | --     | -----------            | ----    | ---------                | 
 * | 0     | OptoCoupled Input 0           | 22     | LVTTL 2                 | 43    | Encoder 3                 |         
 * | 2     | OptoCoupled Input 1           | 23    | LVTTL 3                | 44    | Timer 0                |            
 * | 3     | OptoCoupled Input 3           | 24    | LVTTL 4                | 45    | Timer 1                |            
 * | 4     | OptoCoupled Input 4           | 25    | LVTTL 5                | 46    | Timer 2                |            
 * | 5     | OptoCoupled Input 5           | 26    | LVTTL 6                | 47    | Timer 3                |            
 * | 6     | OptoCoupled Input 6           | 27    | LVTTL 7                | 48    | Timer 4                |            
 * | 7     | OptoCoupled Input 7           | 28    | OptoCoupled Output 0    | 49    | Timer 5                |            
 * | 8    | LVDS Input 0                 | 29    | OptoCoupled Output 1    | 50    | Timer 6                |            
 * | 9    | LVDS Input 1                 | 30    | OptoCoupled Output 2    | 51    | Timer 7                |            
 * | 10    | LVDS Input 2                 | 31    | OptoCoupled Output 3     | 52    | Camera Trigger 0        |            
 * | 11    | LVDS Input 3                 | 32    | OptoCoupled Output 4    | 53    | Camera Trigger 1        |            
 * | 12    | TTL0                       | 33    | OptoCoupled Output 5    | 54    | Camera Trigger 2        |            
 * | 13    | TTL1                       | 34    | OptoCoupled Output 6    | 55    | Camera Trigger 3        |            
 * | 14    | TTL2                       | 35    | OptoCoupled Output 7    | 56    | Camera Trigger 4        |            
 * | 15    | TTL3                       | 36    | LVDS Output 0            | 57    | Camera Trigger 5        |            
 * | 16    | TTL4                       | 37    | LVDS Output 1         | 58    | Camera Trigger 6        |            
 * | 17    | TTL5                       | 38    | LVDS Output 2          | 59    | Camera Trigger 7        |            
 * | 18    | TTL6                       | 39    | LVDS Output 3            | 60    | Acquisition Trigger 0    |            
 * | 19    | TTL7                       | 40    | Encoder 0                | 61    | Acquisition Trigger 1    |            
 * | 20    | LVTTL 0                       | 41    | Encoder 1                | 62    | Acquisition Trigger 2    |            
 * | 21    | LVTTL 1                       | 42    | Encoder 2                | 63    | Acquisition Trigger 3    |            
*/
typedef struct _KYFG_IO_AUX_DATA
{
    uint64_t masked_data;
    uint64_t timestamp;
}KYFG_IO_AUX_DATA;
#pragma pack(pop)

#define KYFG_IO_CONTROLLER_MASKED_IO(masked_data) ((masked_data) & 0xFFFFFFFFFF)
#define KYFG_IO_CONTROLLER_MASKED_ENCODERS(masked_data) (((masked_data) >> 40) & 0xF)
#define KYFG_IO_CONTROLLER_MASKED_TIMERS(masked_data) (((masked_data) >> 44) & 0xFF)
#define KYFG_IO_CONTROLLER_MASKED_CAMERA_TRIGGERS(masked_data) (((masked_data) >> 52) & 0xFF)
#define KYFG_IO_CONTROLLER_MASKED_TRIGGERS(masked_data) (((masked_data) >> 60) & 0xF)


#pragma pack(push, 1)
/**
 * \page Structures Structures
 * \section KYFG_FRAME_AUX_DATA KYFG_FRAME_AUX_DATA
 * Data portion interpretation of Auxiliary data structure, in case of new frame arrival.
 * | Structure Field      | Type         | Description     |
 * | ------------         | ------     | -----------     |
 * | sequence_number    | uint32_t     | Sequential index of the frame within the allocated frame buffer     |
 * | timestamp            | uint64_t     | Frame arrival timestamp in units of nano-seconds (nsec)     |
*/
typedef struct _KYFG_FRAME_AUX_DATA
{
    uint32_t sequence_number;
    uint64_t timestamp;
    uint32_t reserved;
}KYFG_FRAME_AUX_DATA;
#pragma pack(pop)


#define AUX_DATA_MAX_SIZE (4*sizeof(uint32_t))
#pragma pack(push, 1)
/**
 * \page Structures Structures
 * \section KYFG_AUX_DATA KYFG_AUX_DATA
 * Auxiliary data structure holding information of received Auxiliary message.
 * | Structure Field     | Type                     | Description     |
 * | --------------     | ----                             | ----------     |
 * | messageID             | uint32_t                         | Unique ID of a received auxiliary data message. For possible values see Table 2.     |
 * | reserved             | \ref KYBOOL "KYBOOL"             | Unique ID of a received auxiliary data message. For possible values see Table 2.     |
 * | dataSize             | size_t                         | Size, in Bytes, of data delivered with Auxiliary message.    |
 * | u_data             | union                         | See remarks    |
 * | data                 | uint8_t [AUX_DATA_MAX_SIZE]     | Data portion interpretation with no context.    |
 * | io_data            | \ref KYFG_IO_AUX_DATA "KYFG_IO_AUX_DATA"    | Data portion interpretation, in case of callback issued by IO controller    |
 * | frame_data            | \ref KYFG_FRAME_AUX_DATA "KYFG_FRAME_AUX_DATA"    | Data portion interpretation, in case of new frame arrival    |
 * \remarks Starting with version 6.0 the KYFG_AUX_DATA structure definition has changed. Usage of this structure requires client code changes -
            adding "u_data." to where these struct members are accessed, to avoid compilation warning about "nonstandard extension used: 
            nameless struct/union".
 *    | Message identifier                 | Description                                         |
 *    | -------                             | --------                                             |
 *    | KYFG_AUX_MESSAGE_ID_IO_CONTROLLER | The message has been generated by IO Controller.     |
*/
typedef struct _KYFG_AUX_DATA
{
    uint32_t messageID;
    uint8_t  version;

    size_t   dataSize;
    union
    {
        uint8_t              data[AUX_DATA_MAX_SIZE];
        KYFG_IO_AUX_DATA     io_data;                    // messageID == KYFG_AUX_MESSAGE_ID_IO_CONTROLLER
        KYFG_FRAME_AUX_DATA  frame_data;
    } u_data;

    // starting from version 1
    uint64_t    m_InterruptTimestampHw;
    uint64_t    m_InterruptTimestampChrono;
    KYBOOL      m_FromService;
    KYBOOL      m_Simulated;
}KYFG_AUX_DATA;
#pragma pack(pop)
/**
 * \page APIDefinesAndMacros
 * \section FGAuxDataCallback FGAuxDataCallback
 *     The callback function will be called when various auxiliary data is generated. Auxiliary data is passed to user 
    callback as a pointer to the KYFG_AUX_DATA structure parameter. Each Auxiliary data will be interpreted 
    differently according to context and message ID.
 * \code{.c}
    typedef void(KYFG_CALLCONV *FGAuxDataCallback)(
        KYFG_AUX_DATA* pData, 
        void* context);
 * \endcode
*/
typedef void (KYFG_CALLCONV *FGAuxDataCallback)(KYFG_AUX_DATA* pData, void* context);
/// \defgroup CallbackFunctions Callback Functions
/// @{
/**
 * \section KYFG_AuxDataCallbackRegister KYFG_AuxDataCallbackRegister()
 * \brief   Register run-time callback for receiving auxiliary data. The callback will be called when various auxiliary data is 
            generated.
 * \subsection C C
 * \code{.c} FGSTATUS KYFG_AuxDataCallbackRegister(
             FGHANDLE handle, 
             FGAuxDataCallback userFunc,
             void* userContext); \endcode
 * \param   [in]  handle API handle to the chosen device.
 * \param   [in]  userFunc Pointer to callback function implementation
 * \param   [in]  userContext Pointer to user context. This pointer will be passed the 
 *                               callback function. Helps to determine the origin of the 
 *                               function call in the host application 
 * \return  FGSTATUS - Status and error report
 * \subsection Ch C#
 * \code{.sh} void IDevice.AuxDataCallbackRegister(FGAuxDataCallback delegator, Object userContext) \endcode
 * Overloads:
 * \code{.cs} void IGrabber.AuxDataCallbackRegister(FGAuxDataCallback delegator) \endcode
 * \param   [in] delegator  Callback delegate function
 * \param   [in] userContext (Optional) User context. Afterwards this pointer is retrieved when the 
 *                   callback is issued. Helps to determine the origin of function call in host 
 *                   application.
 * \subsection Python Python
 * \code{.py} def KYFG_AuxDataCallbackRegister(handle, userFunc, userContext) \endcode
 * \param   [in]  handle API handle to the chosen device.
 * \param   [in]  userFunc Pointer to callback function implementation
 * \param   [in]  userContext User context. Afterward, this pointer is retrieved when the callback is 
 *                              issued. Helps to determine the origin of the stream in the host 
 *                              application.
 * \return  FGSTATUS - Status and error report
 * \remarks The callback function prototype should be as follows:
 *          \code{.c}  def <user_stream_callback>(streamHandle, userContext) \endcode
 *          streamHandle - API handle to a stream. Type: STREAM_HANDLE
 *          userContext - User context registered using KYFG_AuxDataCallbackRegister
 */
KAYA_API FGSTATUS    KYFG_AuxDataCallbackRegister(FGHANDLE handle, FGAuxDataCallback userFunc, void* userContext);


/**
 * \section KYFG_AuxDataCallbackUnregister KYFG_AuxDataCallbackUnregister()
 * @brief   Unregister previously registered run-time auxiliary data callback function
 * \subsection C C
 * \code{.c} FGSTATUS KYFG_AuxDataCallbackUnregister(
            FGHANDLE handle, 
            FGAuxDataCallback userFunc); \endcode
 * \param   [in] handle  API handle to chosen Frame Grabber
 * \param   [in] userFunc  Callback function prototype
 * \return  FGSTATUS -  Status and error report.
 * \subsection Ch C#
 * \code{.cs} void IDevice.AuxDataCallbackUnregister(FGAuxDataCallback delegator) \endcode
 * \param [in] delegator Callback delegate function
 * \subsection Python Python
 * \code{.py} def KYFG_AuxDataCallbackUnregister(handle, userFunc) \endcode
 * \param [in] handle API handle to the connected device
 * \param [in] userFunc Pointer to the callback function to unregister
 * \return  FGSTATUS -  Status and error report. 
 *
 * \remarks 1. The callback function prototype should be as follows:
 *             \code{.py} def <user_stream_callback>(streamHandle, userContext) \endcode
 *            streamHandle - API handle to a stream. Type: STREAM_HANDLE
 *            userContext - User context registered using KYFG_AuxDataCallbackRegister
 * \remarks 2. Since several Auxiliary data retrieval functions may be registered, the userFunc parameter should be passed to the un-registering 
 *             function to determine which specific function to un-register
 */
KAYA_API FGSTATUS    KYFG_AuxDataCallbackUnregister(FGHANDLE handle, FGAuxDataCallback userFunc);
/// @}
/**
 * \page APIDefinesAndMacros API Defines and Macros
 * \section FGCallback FGCallback
    General runtime transmission callback function prototype. Callbacks are issued whenever a new frame acquisition 
        or generation is complete. Data can be retrieved using the Stream interface function. A callback with buffHandle 
        zero indicates the stop of acquisition for the camera associated with the current buffer.
 * \code{.c}
    typedef void(KYFG_CALLCONV *FGCallback)(
        BUFFHANDLE buffHandle, 
        void* userContext);

 * \endcode
 */
typedef void(KYFG_CALLCONV *FGCallback)(BUFFHANDLE buffHandle, void* userContext);
/// \defgroup CallbackFunctions Callback Functions
/// @{
/**
 * \section KYFG_CallbackRegister KYFG_CallbackRegister() DEPRICATED
 * @brief   Register run-time callback for the Frame Grabber and all connected cameras
 * @details This API function is deprecated. New software should use functions 'KYFG_StreamBufferCallbackRegister()'
 * @param   [in] handle - handle to the connected Frame Grabber
 * @param   [in] userFunc - pointer to the callback function
 * @param   [in] userContext - pointer to the data, to be sent back with callback
 * @return  FGSTATUS - Error code of function execution status
 */
KY_DEPRECATED(KAYA_API FGSTATUS    KYFG_CallbackRegister(FGHANDLE handle, FGCallback userFunc, void* userContext), "Function 'KYFG_CallbackRegister' is deprecated, please use function 'KYFG_StreamBufferCallbackRegister()'.");
/**
 * \section KYFG_CallbackUnregister KYFG_CallbackUnregister() DEPRICATED
 * @brief   Unregister run-time callback for the Frame Grabber and all connected cameras
 * @details This API function is deprecated. New software should use functions 'KYFG_StreamBufferCallbackUnregister()'
 * @param   [in] handle - handle to the connected Frame Grabber
 * @param   [in] userFunc - pointer to the callback function
 * @return  FGSTATUS - Error code of function execution status
 */
KY_DEPRECATED(KAYA_API FGSTATUS    KYFG_CallbackUnregister(FGHANDLE handle, FGCallback userFunc), "Function 'KYFG_CallbackUnregister' is deprecated, please use function 'KYFG_StreamBufferCallbackUnregister()'.");
/// @}


/**
 * \page APIDefinesAndMacros
 * \section CameraCallback CameraCallback
    Runtime acquisition callback function prototype for a specific camera. Callbacks are issued whenever a new frame 
    acquisition/generation is complete from the selected camera. Data can be retrieved using the Stream interface
    functions. A callback with buffHandle zero indicates the stop of acquisition/generation for the camera associated 
    with the registered callback function.
 * \code{.c}
    typedef void(KYFG_CALLCONV *CameraCallback)(
        void* userContext
        BUFFHANDLE buffHandle);
 * \endcode
    
*/
typedef void(KYFG_CALLCONV *CameraCallback)(void* userContext, STREAM_HANDLE streamHandle);
/// \defgroup CallbackFunctions Callback Functions
/// @{
/**
 * \section KYFG_CameraCallbackRegister KYFG_CameraCallbackRegister()
 * \brief   Register a camera runtime acquisition callback function. The callback (userFunc) will be called upon a newly
            received frame, of a valid stream from a specific camera, with appropriate STREAM_HANDLE. Each 
            camera’s callback is serialized and will be held until the end of callback execution. The different camera callbacks 
            are working concurrently. Use the Stream interface functions to handle received data. Additionally, a registered 
            user context pointer is retrieved which consequently can be interpreted by the host application for internal use.
 * \subsection C C
 * \code{.c}    FGSTATUS KYFG_CameraCallbackRegister(
                CAMHANDLE camHandle, 
                CameraCallback userFunc, 
                void* userContext); \endcode
 * \param [in] camHandle     API handle to chosen camera
 * \param [in] userFunc         Pointer to the callback function
 * \param [in] userContext   (optional) Pointer to user context. Afterward, this pointer is 
                        retrieved when the callback is issued. Helps to determine the 
                        origin of the stream in the host application. 
 * \return FGSTATUS Status and error report
 * \subsection Ch C# 
 * \code{.cs} void ICamera.CameraCallbackRegister(CameraCallback delegator, Object userContext); \endcode
 * Overloads:
 * \code{.cs} void ICamera.CameraCallbackRegister(CameraCallback delegator); \endcode
 * \param [in] delegator Delegator to callback function
 * \param [in] userContext User defined context to identify the received callback
 * \subsection Python Python
 * \code{.py} def KYFG_CameraCallbackRegister(camHandle, userFunc, userContext) \endcode
 * \param [in]     camHandle API handle to chosen Frame Grabber
 * \param [in]     userFunc Pointer to callback function
 * \param [in]     int (Optional) currently 0. User context. Afterward, this value is retrieved when the 
                callback is issued. Helps to determine the origin of the stream in the 
                host application
 * \return FGSTATUS Status and error report
 * \remarks      The callback function prototype should be as follows:
 *                 \code{.py} def <user_stream_callback>(streamHandle, userContext) \endcode
 *                 streamHandle - API handle to a stream. Type: STREAM_HANDLE
 *                 userContext - User context registered using KYFG_CameraCallbackRegister
*/
KY_DEPRECATED(KAYA_API FGSTATUS    KYFG_CameraCallbackRegister(CAMHANDLE camHandle, CameraCallback userFunc, void* userContext), "Function 'KYFG_CameraCallbackRegister' is deprecated, please use function 'KYFG_StreamBufferCallbackRegister()'.");
/**
 * \section KYFG_CameraCallbackUnregister KYFG_CameraCallbackUnregister()
 * \brief     Unregisters a previously registered camera runtime acquisition callback function.
 * \subsection C C
 * \code{.c} FGSTATUS KYFG_CallbackUnregister(CAMHANDLE camHandle, CameraCallback userFunc); \endcode
 * \param [in] camHandle API handle to chosen camera
 * \param [in] userFunc Callback function prototype
 * \return FGSTATUS - Status and error report.
 * \subsection ExampleCode Example code: 
 * \brief Example of receiving a callback for a newly acquired frame and copying it to local buffer
 * \code{.c}
         void Stream_callback_func(void* userContext, BUFFHANDLE buffHandle)
         {
        static void* data = NULL;
        static KYBOOL copyingDataFlag = KYFALSE;
        uint64_t width = 0, height = 0, totalFrames = 0, buffSize = 0;
        void* buffData;
        if(0 == buffHandle) // callback with indicator for acquisition stop
        {
         copyingDataFlag = KYFALSE;
         return;
        }
        width = KYFG_GetCameraValueInt(buffHandle, “Width”);
        height = KYFG_GetCameraValueInt(buffHandle, “Height”);
        totalFrames = KYFG_GetGrabberValueInt(buffHandle, "RXFrameCounter");
        buffSize = KYFG_BufferGetSize(buffHandle); // get buffer size
        buffIndex = KYFG_BufferGetFrameIndex(buffHandle);
        buffData = KYFG_BufferGetPtr(buffHandle, buffIndex);// get pointer of buffer data
        if(KYFALSE == copyingDataFlag)
        {
         copyingDataFlag = KYTRUE;
         data = (void*)realloc(data, buffSize); // allocate size for local buffer
         if (NULL == data)
         { 
        return;
         }
         printf(“Callback of buffer %X, width: %d, height: %d, total frames acquired: %d”, 
         buffHandle, width, height, totalFrames);
         memcpy(data, buffData, buffSize); // copy data to local buffer
         //... Show Image with data …
            copyingDataFlag = KYFALSE;
        } 
         }
         int main(int argc, char* argv[]) 
         {
        FGHANDLE handle; 
        CAMHANDLE camHandleArray[MAXBOARDS][KY_MAX_CAMERAS] 
        // maximum KY_MAX_CAMERAS cameras can be connected
        int nDetectedCameras = 0;
        KYFG_CameraScan(handle, camHandleArray, &nDetectedCameras);
        if (nDetectedCameras > 0 )
        { 
         KYFG_CameraCallbackRegister(camHandleArray[grabberIndex], 
        Stream_callback_func, NULL);
        }
        while(1){}
        return 0;
         }
 * \endcode
 * \subsection Ch C#
 * \code{.cs} void ICamera.CameraCallbackUnregister(CameraCallback delegator); \endcode
 * \param [in] delegator Delegator to callback function
 * \subsection Python Pathon
 * \code{.py}     def KYFG_CameraCallbackUnregister(camHandle, userFunc) \endcode
 * \param [in]     camHandle API handle to chosen Frame Grabber
 * \param [in]    userFunc Pointer to callback function
 * \return         FGSTATUS - Status and error report.
 * \remarks     The callback function prototype should be as follows
 *                 \code{.py} def <user_stream_callback>(streamHandle, userContext) \endcode
 *                streamHandle - API handle to a stream. Type: STREAM_HANDLE
 *                userContext - User context registered using KYFG_CameraCallbackRegister
*/
KY_DEPRECATED(KAYA_API FGSTATUS    KYFG_CameraCallbackUnregister(CAMHANDLE camHandle, CameraCallback userFunc), "Function 'KYFG_CameraCallbackUnregister' is deprecated, please use function 'KYFG_StreamBufferCallbackUnregister()'.");
/// @}
/**
 * \page APIDefinesAndMacros
 * \section StreamBufferCallback
 * Runtime acquisition or generation callback function prototype for a specific stream. Callbacks are issued whenever 
    a new frame acquisition or generation is complete from the selected stream. Data can be retrieved using the 
    Stream interface functions. A callback with streamBufferHandle zero indicates the stop of acquisition for stream 
    associated with the registered callback function.
 * \code{.c} 
    typedef void(KYFG_CALLCONV * StreamBufferCallback)(
        STREAM_BUFFER_HANDLE streamBufferHandle, 
        void* userContext);
 * \endcode
*/
typedef void(KYFG_CALLCONV *StreamBufferCallback)(STREAM_BUFFER_HANDLE streamBufferHandle, void* userContext);
/// \defgroup CallbackFunctions Callback Functions
/// @{

/**
 * \section KYFG_StreamBufferCallbackRegister KYFG_StreamBufferCallbackRegister()
 * @brief    Register a stream runtime acquisition callback function. The callback (userFunc) will be called upon a newly
            received frame, of a valid stream, with appropriate STREAM_BUFFER_HANDLE. Each stream’s callback is serialized 
            and will be held until the end of callback execution. The different stream callbacks are working concurrently. Use 
            the Stream interface functions to handle received data. Additionally, a registered user context pointer is retrieved 
            which consequently can be interpreted by the host application for internal use.
 * \subsection C C
 * \code{.c}    FGSTATUS KYFG_StreamBufferCallbackRegister(
                STREAM_HANDLE streamHandle, 
                StreamBufferCallback userFunc, 
                void* userContext); \endcode
 * \param   [in] streamHandle     API handle of a stream
 * \param   [in] userFunc         Callback function prototype
 * \param   [in] userContext    (optional) Pointer to user context. Afterward, this pointer is 
                                retrieved when the callback is issued. Helps to determine the 
                                origin of the stream in the host application. 
 * \return  FGSTATUS - Status and error report.
 * \subsection Ch C#
 * \code{.cs} void IStream.BufferCallbackRegister(StreamBufferCallback^ delegator, Object^ userContext) \endcode
 * Overloads:
 * \code{.cs} void IStream.BufferCallbackRegister(StreamBufferCallback^ delegator) \endcode
 * \param [in] delegator Delegator to callback function
 * \param [in] userContext User defined context to identify the received callback
 * \subsection Python Python
 * \code{.py} def KYFG_StreamBufferCallbackRegister(streamHandle, userFunc, userContext) \endcode
 * \param     [in] streamHandle     API handle of a stream
 * \param     [in] userFunc         Pointer to callback function (see remarks)
 * \param     [in] userContext     User context. Afterward, this pointer is retrieved when the callback is 
                        issued. Helps to determine the origin of the stream in the host 
                        application (see example code).
 * \return  FGSTATUS - Status and error report.
 * \remarks The callback function prototype should be as follows
 *            \code{.py} def <user_stream_callback>(streamHandle, userContext) \endcode
 *            streamHandle - API handle to a stream. Type: STREAM_HANDLE
 *            userContext - User context registered using KYFG_StreamBufferCallbackRegister
 * \subsection ExampleCode Example code
 * \code{.py}
         # User context class
        class StreamInfoStruct:
        def __init__(self):
         self.width = 0
         self.height = 0
         self.callbackCount = 0
         return
        # User callback function according to the prototype
        def Stream_callback_func(buffHandle, userContext):
         streamInfo = cast(userContext, py_object).value
         print('buffer' + str(format(buffHandle, '02x')) + ': height=' + str(streamInfo.height) + ', width=' + 
         str(streamInfo.width) + ', callback count=' + str(streamInfo.callbackCount))
         streamInfo.callbackCount = streamInfo.callbackCount + 1
         return
        # Create and init user context class and register callback function
        streamInfoStruct = StreamInfoStruct()
        streamInfoStruct.width = KYFG_GetCameraValueInt(camHandleArray[grabberIndex][0], "Width")
        streamInfoStruct.height = KYFG_GetCameraValueInt(camHandleArray[grabberIndex][0], "Height")
        (KYFG_StreamBufferCallbackRegister_status,) = 
        KYFG_StreamBufferCallbackRegister(cameraStreamHandle,Stream_callback_func, 
        py_object(streamInfoStruct))

 * \endcode

 */
KAYA_API FGSTATUS KYFG_StreamBufferCallbackRegister(STREAM_HANDLE streamHandle, StreamBufferCallback userFunc, void* userContext);

/**
 * \section KYFG_StreamBufferCallbackUnregister KYFG_StreamBufferCallbackUnregister()
 * \brief   Unregisters a previously registered stream callback function.
 * \subsection C C
 * \code{.c}     FGSTATUS KYFG_StreamBufferCallbackUnregister(
                STREAM_HANDLE streamHandle, 
                StreamBufferCallback userFunc); \endcode
 * \param   [in]     streamHandle      handle to a stream
 * \param   [in]     userFunc          pointer to the callback function
 * \return  FGSTATUS -  Status and error report.
 * \subsection Ch C# 
 * \code{.cs} void IStream.BufferCallbackUnregister(StreamBufferCallback delegator) \endcode
 * \subsection Python Python
 * \code{.py} def KYFG_StreamBufferCallbackUnregister(streamHandle, userFunc) \endcode
 * \param     [in]    streamHandle    API handle of a stream.
 * \param     [in]    userFunc        Pointer to the callback function (see remarks)
 * \return  FGSTATUS -  Status and error report.
 * \remarks    The callback function prototype should be as follows:
 *             \code{.py} def <user_stream_callback>(streamHandle, userContext) \endcode
 *             streamHandle - API handle to a stream. Type: STREAM_HANDLE
 *            userContext - User context registered using KYFG_StreamBufferCallbackRegister

 */
KAYA_API FGSTATUS    KYFG_StreamBufferCallbackUnregister(STREAM_HANDLE streamHandle, StreamBufferCallback userFunc);
/// @}
/**
 * Generic device callbacks:
 * KYDEVICE_EVENT_CAMERA_START_REQUEST - Device detected a remote request to start transmission on a camera.
 *                                         Currently used only for KAYA Chameleon Simulator when remote Frame Grabber sends request for acquisition start. 
 * KYDEVICE_EVENT_CAMERA_CONNECTION_LOST_ID - Detection of connection loss on a previously detected camera.
 *                                         Currently implemented for CoaXPress and CLHS Frame Grabbers only.  
 *                                         For 10Gige Frame Grabbers this event will not be received from callback event.
 * KYDEVICE_EVENT_SYSTEM_TEMPERATURE_ID - This event is sent when device temperature reached a threshold.
 * KYDEVICE_EVENT_CXP2_HEARTBEAT_ID      - CXP 2.0 Heartbeat packet received from connected camera.
 * KYDEVICE_EVENT_CXP2_EVENT_ID          - CXP 2.0 Event packet received from connected camera.
 * KYDEVICE_EVENT_GENCP_EVENT_ID           GenCP Event message id for CLHS
 * KYDEVICE_EVENT_GIGE_EVENTDATA_ID        EVENTDATA Event message id for 10GigE
 */
typedef enum _KYDEVICE_EVENT_ID
{
    KYDEVICE_EVENT_CAMERA_START_REQUEST = 0,
    KYDEVICE_EVENT_CAMERA_CONNECTION_LOST_ID = 1,
    KYDEVICE_EVENT_SYSTEM_TEMPERATURE_ID = 2,
    KYDEVICE_EVENT_CXP2_HEARTBEAT_ID = 3,
    KYDEVICE_EVENT_CXP2_EVENT_ID = 4,
    KYDEVICE_EVENT_GENCP_EVENT_ID = 5,
    KYDEVICE_EVENT_GIGE_EVENTDATA_ID = 6,
}KYDEVICE_EVENT_ID;

#pragma pack(push, 1)
/**
 * \page Structures Structures
 * \section KYDEVICE_EVENT KYDEVICE_EVENTs
 *     Device event structure holds information of a received event. Pointer to this base structure is passed to the 
    \ref KYDeviceEventCallBack "KYDeviceEventCallBack" function. Its ‘eventId’ field should be used to determine what concrete event is passed and 
    the pointer should be C-casted to a pointer to a corresponding derived structure.
 * | Structure Field    | Type                 | Description     |
 * | ----------------    | ---                 | ---------     |
 * | eventId            | KYDEVICE_EVENT_ID    | Unique ID of received device event. For possible values see the table below     |
 *
 * Device event ID possible values
 *
 * | Value                                         | Description    |
 * | -----------                                | -------         |
 * | KYDEVICE_EVENT_CAMERA_START_REQUEST         | The device detected a remote request to start transmission on a camera |
 * | KYDEVICE_EVENT_CAMERA_CONNECTION_LOST_ID     | The device detected a remote request to start transmission on a lost camera |
 * | KYDEVICE_EVENT_SYSTEM_TEMPERATURE_ID         | Device temperature reached a threshold |
 * | KYDEVICE_EVENT_CXP2_HEARTBEAT_ID             | CXP 2.0 Heartbeat packet received from connected camera     |
 * | KYDEVICE_EVENT_CXP2_EVENT_ID                 | CXP 2.0 Event packet received from connected camera     |
 * | KYDEVICE_EVENT_GENCP_EVENT_ID                 | GenCP Event message id for CLHS     |
 * | KYDEVICE_EVENT_GIGE_EVENTDATA_ID            | EVENTDATA Event message id for 10GigE     |

*/
typedef struct _KYDEVICE_EVENT
{
    KYDEVICE_EVENT_ID eventId;
}KYDEVICE_EVENT;
#pragma pack(pop)

#pragma pack(push, 1)
typedef struct _KYDEVICE_EVENT_CAMERA_START
{
    // KYDEVICE_EVENT_CAMERA_START derived from KYDEVICE_EVENT:
    KYDEVICE_EVENT deviceEvent;

    // Specific KYDEVICE_EVENT_CAMERA_START members:
    CAMHANDLE camHandle;
}KYDEVICE_EVENT_CAMERA_START;
#pragma pack(pop)

#pragma pack(push, 1)
typedef struct _KYDEVICE_EVENT_CAMERA_CONNECTION_LOST
{
    // KYDEVICE_EVENT_CAMERA_CONNECTION_LOST derived from KYDEVICE_EVENT:
    KYDEVICE_EVENT deviceEvent;

    // Specific KYDEVICE_EVENT_CAMERA_CONNECTION_LOST members:
    CAMHANDLE camHandle;
    size_t    iDeviceLink;
    size_t    iCameraLink;
}KYDEVICE_EVENT_CAMERA_CONNECTION_LOST;
#pragma pack(pop)

typedef enum _KYDEVICE_EVENT_SYSTEM_TEMPERATURE_THRESHOLD_ID
{
    KYDEVICE_EVENT_SYSTEM_TEMPERATURE_NORMAL = 0,
    KYDEVICE_EVENT_SYSTEM_TEMPERATURE_WARNING = 1,
    KYDEVICE_EVENT_SYSTEM_TEMPERATURE_CRITICAL = 2,
}KYDEVICE_EVENT_SYSTEM_TEMPERATURE_THRESHOLDS_ID;

#pragma pack(push, 1)
typedef struct _KYDEVICE_EVENT_SYSTEM_TEMPERATURE
{
    // KYDEVICE_EVENT_SYSTEM_TEMPERATURE derived from KYDEVICE_EVENT:
    KYDEVICE_EVENT deviceEvent;

    KYDEVICE_EVENT_SYSTEM_TEMPERATURE_THRESHOLDS_ID temperatureThresholdId;

}KYDEVICE_EVENT_SYSTEM_TEMPERATURE;
#pragma pack(pop)

#pragma pack(push, 1)
typedef struct _KYDEVICE_EVENT_CXP2_HEARTBEAT
{
    // KYDEVICE_EVENT_CXP2_HEARTBEAT derived from KYDEVICE_EVENT:
    KYDEVICE_EVENT deviceEvent;

    // Specific KYDEVICE_EVENT_CXP2_HEARTBEAT members:
    CAMHANDLE camHandle;
    KY_CXP2_HEARTBEAT heartBeat;
}KYDEVICE_EVENT_CXP2_HEARTBEAT;
#pragma pack(pop)

#pragma pack(push, 1)
typedef struct _KYDEVICE_EVENT_CXP2_EVENT
{
    // KYDEVICE_EVENT_CXP2_EVENT derived from KYDEVICE_EVENT:
    KYDEVICE_EVENT deviceEvent;

    // Specific KYDEVICE_EVENT_CXP2_EVENT members:
    CAMHANDLE camHandle;
    KY_CXP2_EVENT cxp2Event;
}KYDEVICE_EVENT_CXP2_EVENT;
#pragma pack(pop)

#pragma pack(push, 1)
// used for KYDEVICE_EVENT_GENCP_EVENT_ID
#define KY_GENCP_EVENT_MAX_DATA_SIZE 1024

typedef struct _KYDEVICE_EVENT_GENCP_EVENT
{
    // KYDEVICE_EVENT_GENCP_EVENT derived from KYDEVICE_EVENT:
    KYDEVICE_EVENT deviceEvent;

    // Specific KYDEVICE_EVENT_GENCP_EVENT members:
    int srcDevicePort;
    struct KY_GENCP_EVENT
    {
        uint16_t eventSize;    // Size of event data object in bytes including event_size, event_id, timestamp and optional data.
        uint16_t eventId;      // The event_id is a number identifying an event source.
        uint64_t timestamp;     // 64 bit timestamp value in ns as defined in the timestamp bootstrap register.
        uint8_t data[KY_GENCP_EVENT_MAX_DATA_SIZE];  // data payload with valid size of 'dataSize'
    }gencpEvent;
    
}KYDEVICE_EVENT_GENCP_EVENT;
#pragma pack(pop)

#pragma pack(push, 1)

// used for KYDEVICE_EVENT_GIGE_EVENTDATA_ID
#define KY_GIGE_EVENTDATA_EVENT_MAX_DATA_SIZE 512

typedef struct _KYDEVICE_EVENT_GIGE_EVENTDATA
{
    // KYDEVICE_EVENT_GIGE_EVENTDATA derived from KYDEVICE_EVENT:
    KYDEVICE_EVENT deviceEvent;

    // Specific KYDEVICE_EVENT_GIGE_EVENTDATA members:
    int srcDevicePort;
    struct KY_GIGE_EVENTDATA_EVENT 
    {
        uint16_t eventSize;      // Size of event data object in bytes including event_size, event_id, timestamp and optional data.
        uint16_t eventId;        // The event_id is a number identifying an event source.
        uint16_t streamChannel;
        uint16_t blockId;
        uint64_t timestamp;       // 64 bit timestamp value in ns as defined in the timestamp bootstrap register.
        uint8_t data[KY_GIGE_EVENTDATA_EVENT_MAX_DATA_SIZE];  // data payload with valid size of 'dataSize'
    }gigeEvent;
}KYDEVICE_EVENT_GIGE_EVENTDATA;
#pragma pack(pop)
/**
 * \page APIDefinesAndMacros
 * \section KYDeviceEventCallBack KYDeviceEventCallBack
 *     The callback function will be called when various device events are generated. Details of an event are passed to 
    user callback as a pointer to the KYDEVICE_EVENT structure. Each event data should be interpreted according to 
    event ID.
 * \code{.c}
    typedef void(KYFG_CALLCONV * KYDeviceEventCallBack)(
        void* context, 
        KYDEVICE_EVENT* pEvent)
 * \endcode
*/
typedef void(KYFG_CALLCONV *KYDeviceEventCallBack)(void* userContext, KYDEVICE_EVENT* pEvent);
/// \defgroup CallbackFunctions Callback Functions
/// @{
/**
 * \section KYDeviceEventCallBackRegister KYDeviceEventCallBackRegister()
 * @brief Register a generic runtime callback function. The callback (userFunc) will be called to inform the user application 
 *        about various events in the system. See KYDEVICE_EVENT for more details.
 * \subsection C C
 * \code{.c} FGSTATUS KYDeviceEventCallBackRegister(
    FGHANDLE handle, 
    KYDeviceEventCallBack userFunc,
    void* userContext); \endcode
 * @param [in] handle - API handle to chosen Frame Grabber
 * @param [in] userFunc - Pointer to callback function implementation
 * @param [in] userContext - Pointer to user context. This pointer is passed to the user’s 
 *             callback function as the first parameter.
 * @return FGSTATUS - Status and error report.
 * \subsection СS C#
 * \code{.sh} void IDevice.EventCallBackRegister(DeviceEventCallBack^ deviceEventDelegator); \endcode 
 * \subsection Python Python
 * @param [in] handle - API handle to the connected device.
 * @param [in] userFunc - Pointer to callback function implementation.
 * @param [in] userContext - User context. Afterward, this pointer is retrieved when the callback is 
 *             issued. Helps to determine the origin of the stream in the host 
 *             application
 * @return FGSTATUS - Status and error report.
 * @remarks The callback function prototype should be as follows: \code{.py} def <user_stream_callback>(streamHandle, userContext) \endcode
 * @remarks streamHandle - API handle to a stream. Type: STREAM_HANDLE
 * @remarks userContext - User context registered using KYDeviceEventCallBackRegiste
*/ 
KAYA_API FGSTATUS KYDeviceEventCallBackRegister(FGHANDLE handle, KYDeviceEventCallBack userFunc, void* userContext);
/**
 * \section KYDeviceEventCallBackUnregister KYDeviceEventCallBackUnregister()
 * @brief Unregisters a previously registered user runtime callback function.
 * \subsection C C
 * \code{.c} FGSTATUS KYDeviceEventCallBackUnregister(
    FGHANDLE handle, 
    FGAuxDataCallback userFunc); \endcode
 * @param [in] handle - API handle to chosen Frame Grabber
 * @param [in] userFunc - Callback function prototype.
 * @return FGSTATUS - Status and error report.
 * \subsection СS C#
 * \code{.sh} void IDevice.EventCallBackUnregister(DeviceEventCallBack^ deviceEventDelegator); \endcode
 * \subsection Python Python
 * \code def KYDeviceEventCallBackUnregister(handle, userFunc) \endcode
 * @param [in] handle - API handle to the connected device.
 * @param [in] userFunc - Pointer to the callback function to unregister. See remarks.
 * @return FGSTATUS - Status and error report.
 * @remarks 1. Since several callback functions may be registered, the userFunc parameter should be passed to the un-registering function to 
            determine which specific function to un-register.
 * @remarks 2. Any of the callback functions described in this section should perform minimally necessary tasks and return as soon as possible, avoiding 
 *           long-running I/O operations. For example, KYFG_CameraWriteReg is an I/O operation that involves signal round-trip to the camera and 
 *           waiting for the camera's acknowledgment. It is advised to move the operations to a separated thread using a function, such as 
 *           KYFG_CameraWriteReg, for a direct write data buffer to the selected camera.
 * @remarks 3. The callback function prototype should be as follows:
 *            \code{.py} def <user_stream_callback>(streamHandle, userContext) \endcode
 *            streamHandle - API handle to a stream. Type: STREAM_HANDLE
 *            userContext - User context registered using KYDeviceEventCallBackRegister

*/
KAYA_API FGSTATUS KYDeviceEventCallBackUnregister(FGHANDLE handle, KYDeviceEventCallBack userFunc);
/// @}
/**
 * @brief   Creates new stream object bound to given open camera
 * @details Collects information from the camera such as value of "Width", "Height", "PixelFormat" and Frame Grabber configuration parameters
 *          such as "SegmentsPerBuffer" in order to initialize internal data structure(s) required to manage a video stream
 *          Stream created with this function requires user to provide and manage buffers using KYFG_BufferToQueue() and/or KYFG_BufferQueueAll() API calls
 * @pre     Camera specified by 'camHandle' must be open
 * @param   [in] camHandle - handle of an open camera
 * @param   [out] pStreamHandle - pointer to STREAM_HANDLE variable that will be filled with handle of newly created stream
 * @param   [in] streamIndex - reserved for future use, must be 0
 * @return  FGSTATUS - Error code of function execution status
 */
KAYA_API FGSTATUS KYFG_StreamCreate(CAMHANDLE camHandle, STREAM_HANDLE *pStreamHandle, int streamIndex); //streamIndex currently must be 0

/**
 * @brief   Link all announced frame buffers into a stream to form continuous cyclic buffer
 * @param   [in] streamHandle - handle of a previously created stream
 * @return  FGSTATUS - Error code of function execution status
 */
KAYA_API FGSTATUS KYFG_StreamLinkFramesContinuously(STREAM_HANDLE streamHandle);

/**
 * @brief   Creates internal stream object bound to given open camera with automatic video buffer allocation and management.
 * @details Collects information from given open camera such as value of "Width", "Height" and "PixelFormat" and Frame Grabber configuration parameters
 *          such as "SegmentsPerBuffer" to initialize internal data structure(s) required to manage a video stream.
 *          Stream created with this function automatically manages buffers and doesn't accept KYFG_BufferToQueue() and KYFG_BufferQueueAll() API calls
 * @pre     Camera specified by 'camHandle' must be open.
 * @param   [in] camHandle - handle of an open camera.
 * @param   [out] pStreamHandle - pointer to STREAM_HANDLE variable that will be filled with handle of newly created stream.
 * @param   [in] frames - number of allocated video frames required.
 * @param   [in] streamIndex - reserved for future use, must be 0
 * @return  FGSTATUS - Error code of function execution status
 */
KAYA_API FGSTATUS KYFG_StreamCreateAndAlloc(CAMHANDLE camHandle, STREAM_HANDLE *pStreamHandle, uint32_t frames, int streamIndex);

KY_DEPRECATED2(KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_BufferAlloc(CAMHANDLE camHandle, BUFFHANDLE *pBuffHandle, uint32_t frames) KY_FORCE_INLINE_GCC, "Function 'KYFG_BufferAlloc' is deprecated, please use function 'KYFG_StreamCreateAndAlloc()'.");
KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_BufferAlloc(CAMHANDLE camHandle, BUFFHANDLE *pBuffHandle, uint32_t frames)
{
    return KYFG_StreamCreateAndAlloc(camHandle, pBuffHandle, frames, 0);
}

/**
 * @brief Create a single buffer, representing a single frame. The buffer will be associated  with the pre-defined stream
 */
KAYA_API FGSTATUS KYFG_BufferAllocAndAnnounce(STREAM_HANDLE streamHandle, size_t nBufferSize, void * pPrivate, STREAM_BUFFER_HANDLE * pBufferHandle);

/**
 * @brief Submit a user buffer, representing a single frame, for a pre-defined stream
 */
KAYA_API FGSTATUS KYFG_BufferAnnounce(STREAM_HANDLE streamHandle, void * pBuffer, size_t nBufferSize, void * pPrivate, STREAM_BUFFER_HANDLE * pBufferHandle);

/**
 * @brief   Announce a buffer per chunk to selected stream
 * @param   [in] streamHandle - handle of stream
 * @param   [in]   pDataChunkArr       Array of data chunks.
 * @param   [in]   nChunks             Number of chunks in pDataChunkArr.
 * @param   [in] streamBufferHandle - handle of previously announced buffer
 */
KAYA_API FGSTATUS KYFG_BufferAnnounceChunks(STREAM_HANDLE streamHandle, const BUFFER_DATA_CHUNK* pDataChunkArr, size_t nChunks, void * pPrivate, STREAM_BUFFER_HANDLE * pBufferHandle);

/**
 * @brief   Revoke and delete resources of previously announced buffer
 * @pre     The buffer should be announced to 'streamHandle' stream
 *          Acquisition should be stopped
 * @param   [in] streamHandle - handle of stream
 * @param   [in] streamBufferHandle - handle of previously announced buffer
 */
KAYA_API FGSTATUS KYFG_BufferRevoke(STREAM_HANDLE streamHandle, STREAM_BUFFER_HANDLE streamBufferHandle);

/**
 * \page Enumerations Enumerations
 * \section KY_STREAM_INFO_CMD KY_STREAM_INFO_CMD
 * Stream information.
 * | Structure Field                                  | Value | Description |
 * | ---------------                                  | ----- | ----------- |
 * | KY_STREAM_INFO_PAYLOAD_SIZE                    |   7     | Size of the expected data in bytes, of currently configured stream |
 * | KY_STREAM_INFO_BUF_ALIGNMENT                    |   13     | The alignment of memory allocated for a buffer, in bytes |
 * | KY_STREAM_INFO_PAYLOAD_SIZE_INCREMENT_FACTOR    |  1000    | Payload size (should be divisible by increment factor) |
 * | KY_STREAM_INFO_ACTUAL_PAYLOAD_SIZE                |  1001    | Size of the actual data in bytes that will be delivered, for current device configurations (change every update of parameters which define the image dimensions)|
 * | KY_STREAM_INFO_BUF_COUNT                        |  2000    | The number of buffers in the stream |
 * | KY_STREAM_INFO_INSTANTFPS                        |  2001    | Last calculated FPS. Valid after at least two frames have been acquired |
 * | KY_STREAM_INFO_CAMHANDLE                        |  2002    | CAMHANDLE of the camera to which this stream belongs. May be NULL_CAMHANDLE in future enhancements with multi-camera streams |
*/
typedef enum _KY_STREAM_INFO_CMD
{
    KY_STREAM_INFO_PAYLOAD_SIZE                     =  7,   /* SIZET      Size of the expected data in bytes, of currently configured stream */
    KY_STREAM_INFO_BUF_ALIGNMENT                    = 13,   /* SIZET      Buffer memory alignment in bytes */
    KY_STREAM_INFO_PAYLOAD_SIZE_INCREMENT_FACTOR    = 1000, /* SIZET      Payload size should be divisible by increment factor */
    KY_STREAM_INFO_ACTUAL_PAYLOAD_SIZE              = 1001, /* SIZET      Size of the actual data in bytes that will be delivered, for current device configurations (change every update of parameters which define the image dimensions ) */
    KY_STREAM_INFO_BUF_COUNT                        = 2000, /* SIZET      Number of buffers in the stream */
    KY_STREAM_INFO_INSTANTFPS                       = 2001, /* FLOAT64    Last calculated FPS. Valid after at least two frames have been acquired*/
    KY_STREAM_INFO_CAMHANDLE                        = 2002, /* CAMHANDLE  CAMHANDLE of the camera to which this stream belongs. May be NULL_CAMHANDLE in future enhancements with multi-camera streams*/
    KY_STREAM_INFO_WIDTH                            = 2003, /* INT64      Frame width*/
    KY_STREAM_INFO_HEIGHT                           = 2004, /* INT64      Frame height*/
}KY_STREAM_INFO_CMD;
/**
 * \page Enumerations Enumerations
 * \section KY_ACQ_QUEUE_TYPE
 * Queued Buffer configuration information.
 * | Structure Field        | Value | Description |
 * | ---------------         | ----- | ----------- |
 * | KY_ACQ_QUEUE_INPUT        |  0     | Buffers in the INPUT queue are ready to be filled with data |
 * | KY_ACQ_QUEUE_OUTPUT    |  1     | Buffers in the OUTPUT queue have been filled and awaiting user processing. This queue is filled internally by the library. The ability of the application to move buffers to this queue is reserved for future library enhancements and currently will result in the error code FGSTATUS_DESTINATION_QUEUE_NOT_SUPPORTED |
 * | KY_ACQ_QUEUE_UNQUEUED    |  2     | Buffers in the UNQUEUED set have been announced but are inactive for the acquisition mechanism. By default, all buffers are placed in the UNQUEUED set. |
 * | KY_ACQ_QUEUE_AUTO        |  3     | Buffers in the AUTO queue are managed automatically, in a cyclic matter, without the need for the user to re-queue them. |
*/
typedef enum _KY_ACQ_QUEUE_TYPE
{
    KY_ACQ_QUEUE_INPUT,                 // buffers in INPUT queue are ready to be filled with data 
    KY_ACQ_QUEUE_OUTPUT,                // buffers in OUTPUT queue have been filled with data and awaiting user processing
    KY_ACQ_QUEUE_UNQUEUED,              // buffers in UNQUEUED set have been announced but are inactive for acquisition mechanism. By default all buffers are placed in UNQUEUED set
    KY_ACQ_QUEUE_AUTO,                  // buffers in AUTO queue are managed automatically, in a cyclic matter, without the need for user to re-queue them 
}KY_ACQ_QUEUE_TYPE;

/**
 * @brief retrieve information on requested pre-defined stream
 */
KAYA_API FGSTATUS KYFG_StreamGetInfo(STREAM_HANDLE streamHandle, KY_STREAM_INFO_CMD cmdStreamInfo, void *pInfoBuffer, size_t *pInfoSize, KY_DATA_TYPE *pInfoType);

/**
 * \page Enumerations Enumerations
 * \section KY_STREAM_BUFFER_INFO_CMD KY_STREAM_BUFFER_INFO_CMD
 * Stream Buffer configuration information.
 * | Structure Field                      | Value | Description |
 * | ---------------                      | ----- | ----------- |
 * | KY_STREAM_BUFFER_INFO_BASE         |      0    | Base address of the buffer memory |
 * | KY_STREAM_BUFFER_INFO_SIZE         |      1    | Size of the buffer in bytes |
 * | KY_STREAM_BUFFER_INFO_USER_PTR        |      2    | Private data pointer for the stream buffer |
 * | KY_STREAM_BUFFER_INFO_TIMESTAMP    |      3    | Timestamp the buffer was acquired |
 * | KY_STREAM_BUFFER_INFO_INSTANTFPS    |      4    | Instant FPS calculated from this and the previous timestamp |
 * | KY_STREAM_BUFFER_INFO_IMAGEID        |      16    | Image ID as reported by a transport protocol, e.g. CXP's "source image index" |
 * | KY_STREAM_BUFFER_INFO_ID            |  1000    | Unique id of buffer in the stream |
 * | KY_STREAM_BUFFER_INFO_STREAM_HANDLE|  1001    | Handle of a stream to which this buffer belongs |
*/

typedef enum _KY_STREAM_BUFFER_INFO_CMD
{
    KY_STREAM_BUFFER_INFO_BASE                            = 0,    /* PTR        Base address of the buffer memory */
    KY_STREAM_BUFFER_INFO_SIZE                            = 1,    /* SIZET      Size of the buffer in bytes */
    KY_STREAM_BUFFER_INFO_USER_PTR                        = 2,    /* PTR        Private data pointer to the stream buffer */
    KY_STREAM_BUFFER_INFO_TIMESTAMP                       = 3,    /* UINT64     Timestamp the buffer was acquired in nanoseconds*/
    KY_STREAM_BUFFER_INFO_INSTANTFPS                      = 4,    /* FLOAT64    Instant FPS calculated from current and previous timestamp */
    KY_STREAM_BUFFER_INFO_IMAGEID                         = 16,   /* UINT64     Image ID as reported by a transport protocol, e.g. CXP's "source image index" */
    KY_STREAM_BUFFER_INFO_ID                              = 1000, /* UINT32     Unique ID of the buffer in a stream */
    KY_STREAM_BUFFER_INFO_STREAM_HANDLE                   = 1001, /* STREAM_HANDLE  the handle of a stream to which this buffer belongs */
    KY_STREAM_BUFFER_INFO_INTERRUPTTIMESTAMP_HW           = 1002, /* UINT64     Timestamp the hw interrupt for this buffer was received in nanoseconds (hardware register)*/
    KY_STREAM_BUFFER_INFO_INTERRUPTTIMESTAMP_CHRONO       = 1003, /* UINT64     Timestamp the hw interrupt for this buffer was received in nanoseconds (std::chrono::steady_clock)*/
}KY_STREAM_BUFFER_INFO_CMD;

KAYA_API FGSTATUS KYFG_BufferGetInfo(STREAM_BUFFER_HANDLE streamBufferHandle, KY_STREAM_BUFFER_INFO_CMD cmdStreamBufferInfo, void *pInfoBuffer, size_t *pInfoSize, KY_DATA_TYPE *pInfoType);


KAYA_API FGSTATUS KYFG_BufferToQueue(STREAM_BUFFER_HANDLE streamBufferHandle, KY_ACQ_QUEUE_TYPE dstQueue);


KAYA_API FGSTATUS KYFG_BufferQueueAll(STREAM_HANDLE streamHandle, KY_ACQ_QUEUE_TYPE srcQueue, KY_ACQ_QUEUE_TYPE dstQueue);

enum SUBMIT_BUFF_FLAGS
{
    SUBMIT_BUFF_REGULAR_BUFFER = 0x00,
    SUBMIT_BUFF_PHYSICAL_ADDRESS = 0x01,                // provided buffer addresses are physical addresses
};
KAYA_API FGSTATUS    KYFG_BufferSubmit(CAMHANDLE camHandle, STREAM_HANDLE *streamHandle, void** bufferPtrArray, unsigned int frames, unsigned int frameSize, unsigned int flags);


// Deletes the stream and frees all associated  resources 
KAYA_API FGSTATUS    KYFG_StreamDelete(STREAM_HANDLE streamHandle);

KY_DEPRECATED2(KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_BufferDelete(BUFFHANDLE buffHandle) KY_FORCE_INLINE_GCC, "Function 'KYFG_BufferDelete' is deprecated, please use function 'KYFG_StreamDelete'.");
KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_BufferDelete(BUFFHANDLE buffHandle)
{
    return KYFG_StreamDelete(buffHandle);
}

/**
 * @brief   Retrieves the pointer to a specified frame
 * @param   [in] streamHandle - handle to the allocated buffer
 * @param   [in] frame - the number of frame to retrieve its pointer [1..<num_of_frames>]
 * @return  pointer to a specified frame
 */
KAYA_API void*    KYFG_StreamGetPtr(STREAM_HANDLE streamHandle, uint32_t frame);
KY_DEPRECATED2(KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API void* KYFG_BufferGetPtr(BUFFHANDLE buffHandle, uint32_t frame) KY_FORCE_INLINE_GCC, "Function 'KYFG_BufferGetPtr' is deprecated, please use function 'KYFG_StreamGetPtr'.");
KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API void* KYFG_BufferGetPtr(BUFFHANDLE buffHandle, uint32_t frame)
{
    return KYFG_StreamGetPtr(buffHandle, frame);
}

// Retrieves the buffer's frame size (in bytes)
KAYA_API int64_t    KYFG_StreamGetSize(STREAM_HANDLE streamHandle);
KY_DEPRECATED2(KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API int64_t KYFG_BufferGetSize(BUFFHANDLE buffHandle) KY_FORCE_INLINE_GCC, "Function 'KYFG_BufferGetSize' is deprecated, please use function 'KYFG_StreamGetSize'.");
KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API int64_t KYFG_BufferGetSize(BUFFHANDLE buffHandle)
{
    return KYFG_StreamGetSize(buffHandle);
}

// Retrieves the last index of acquired frame from chosen buffer
KAYA_API int KYFG_StreamGetFrameIndex(STREAM_HANDLE streamHandle);
KY_DEPRECATED2(KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API int KYFG_BufferGetFrameIndex(BUFFHANDLE buffHandle) KY_FORCE_INLINE_GCC, "Function 'KYFG_BufferGetFrameIndex' is deprecated, please use function 'KYFG_StreamGetFrameIndex'.");
KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API int KYFG_BufferGetFrameIndex(BUFFHANDLE buffHandle)
{
    return KYFG_StreamGetFrameIndex(buffHandle);
}

/**
 * @brief   Retrieves the pointer to an auxiliary  data of a specified frame
 * @param   [in] streamHandle - handle to the allocated stream
 * @param   [in] frame - the number of frame to retrieve its pointer [1..<num_of_frames>]
 * @param   [out] pAuxData - pointer of an auxiliary  data to be filled
 * @return  FGSTATUS - Error code of function execution status
 */
KAYA_API FGSTATUS KYFG_StreamGetAux(STREAM_HANDLE streamHandle, int frame, KYFG_AUX_DATA* pAuxData);

KY_DEPRECATED2(KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_BufferGetAux(BUFFHANDLE buffHandle, int frame, KYFG_AUX_DATA* pAuxData) KY_FORCE_INLINE_GCC, "Function 'KYFG_BufferGetAux' is deprecated, please use function 'KYFG_StreamGetAux'.");
KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_BufferGetAux(BUFFHANDLE buffHandle, int frame, KYFG_AUX_DATA* pAuxData)
{
    return KYFG_StreamGetAux(buffHandle, frame, pAuxData);
}

// <-- Low level registers access -->
// Read/Write to chosen port of the device
KAYA_API FGSTATUS KYFG_ReadPortReg(FGHANDLE handle, int port, uint64_t address, uint32_t* pData);
KAYA_API FGSTATUS KYFG_WritePortReg(FGHANDLE handle, int port, uint64_t address, uint32_t data);
/**
 * @brief   Read block of data to chosen port of the device.
 * @param   [in] handle - handle to the connected device
 * @param   [in] port - device port through which data will be sent
 * @param   [in] address - destination address on the remote device
 * @param   [out] data - data buffer, its length  is specified by size parameter
 * @param   [in,out] size - block size to process. Size will return the number of the proccessed bytes
 * @return  FGSTATUS - Error code of function execution status
 */
KAYA_API FGSTATUS KYFG_ReadPortBlock(FGHANDLE handle, int port, uint64_t address, void* pBuffer, uint32_t* pSize);
/**
 * @brief   Write block of data to chosen port of the device.
 * @param   [in] handle - handle to the connected device
 * @param   [in] port - device port through which data will be sent
 * @param   [in] address - destination address on the remote device
 * @param   [in] data - data buffer, its length  is specified by size parameter
 * @param   [in,out] size - block size to process. Size will return the number of the proccessed bytes
 * @return  FGSTATUS - Error code of function execution status
 */
KAYA_API FGSTATUS KYFG_WritePortBlock(FGHANDLE handle, int port, uint64_t address, const void* pBuffer, uint32_t* pSize);

typedef enum _PORT_STATUS
{
    PORT_DISCONNECTED         = 0x00,   // the port is disconnected, no link has been established
    PORT_SYNCHRONIZED         = 0x01,   // port link is synchronized and awaiting connection
    PORT_CONNECTING           = 0x10,     // a connection is trying to be established on port
    PORT_CONNECTED            = 0x11       // port is connected and assigned to camera 
}PORT_STATUS;

KAYA_API FGSTATUS KYFG_GetPortStatus(FGHANDLE handle, int port, PORT_STATUS* portStatus);

// Direct Read/Write to an address of connected device
KAYA_API FGSTATUS KYFG_DeviceDirectHardwareRead(FGHANDLE handle, uint64_t address, void* pBuffer, uint32_t bytes);
KAYA_API FGSTATUS KYFG_DeviceDirectHardwareWrite(FGHANDLE handle, uint64_t address, const void* pBuffer, uint32_t bytes);

// Direct Read/Write to registers of a connected device
KAYA_API FGSTATUS KYFG_GrabberReadReg(FGHANDLE handle, uint64_t address, void* pBuffer, uint32_t* pSize);
KAYA_API FGSTATUS KYFG_GrabberWriteReg(FGHANDLE handle, uint64_t address, const void* pBuffer, uint32_t* pSize);

// Read/Write to registers of a connected Camera (no XML file has to be present)
KAYA_API FGSTATUS KYFG_CameraReadReg(CAMHANDLE camHandle, uint64_t address, void* pBuffer, uint32_t* pSize);
KAYA_API FGSTATUS KYFG_CameraWriteReg(CAMHANDLE camHandle, uint64_t address, const void* pBuffer, uint32_t* pSize);




/**
 * @brief   Send Event Message as specified by GenCP for CLHS, or EVENTDATA message for 10GigE
 * @details Event message from remote device can be received using "KYDeviceEventCallBackRegister" event id "KYDEVICE_EVENT_GENCP_EVENT_ID" for CLHS, and "KYDEVICE_EVENT_GIGE_EVENTDATA_ID" for 10GigE
 * @param   [in] handle - handle to the connected device
 * @param   [in] port - device port through which data will be sent
 * @param   [in] eventId - id of the event message
 * @param   [in] pBuffer - event data payload, its length is specified by size parameter
 * @param   [in] bufferSize - event data size to process
 * @return  FGSTATUS - Error code of function execution status
 */
KAYA_API FGSTATUS KYFG_DevicePortSendEventMessage(FGHANDLE handle, int port, uint32_t eventId, const void* pBuffer, uint32_t bufferSize);

/**
 * @brief   Send Event Message as specified by GenCP for CLHS, or EVENTDATA message for 10GigE
 * @details Event message from remote device can be received using "KYDeviceEventCallBackRegister" event id "KYDEVICE_EVENT_GENCP_EVENT_ID" for CLHS, and "KYDEVICE_EVENT_GIGE_EVENTDATA_ID" for 10GigE
 * @param   [in] camHandle - Camera handle
 * @param   [in] eventId - id of the event message
 * @param   [in] pBuffer - event data payload, its length is specified by size parameter
 * @param   [in] bufferSize - event data size to process
 * @return  FGSTATUS - Error code of function execution status
 */
KAYA_API FGSTATUS KYFG_CameraSendEventMessage(CAMHANDLE camHandle, uint32_t eventId, const void* pBuffer, uint32_t bufferSize);


// <-- Camera connection -->
// Scans for the cameras connected to the Frame Grabber
KY_DEPRECATED(KAYA_API FGSTATUS KYFG_CameraScan(FGHANDLE handle, CAMHANDLE *camHandleArray, int *detectedCameras), "Function 'KYFG_CameraScan' is deprecated, please use function 'KYFG_UpdateCameraList'. Project should be loaded using function 'KYFG_OpenEx'.");

/**
 * @brief   Updates the list of cameras connected to the device. Open camera handles are not affected by this function and retained at the same places of array where they were returned by previous call except for camera(s) that were closed between calls.
 * @param   [in] handle - handle to the connected device
 * @param   [out] pCamHandleArray - pointer to an array of CAMHANDLE elements
 * @param   [in,out] pArraySize - pointer to integer. Must be set to number of elements allocated in the 'pCamHandleArray'. After successful function return indicates number of elements that were filled
 * @return  FGSTATUS - Error code of function execution status
 */
KAYA_API FGSTATUS KYFG_UpdateCameraList(FGHANDLE handle, CAMHANDLE *pCamHandleArray, int *pArraySize);

#pragma pack(push, 1)
typedef struct _KYFGLib_CameraScanParameters
{
    uint32_t version; // Version of this structure definition, must be 1

    // since version 1:
    CAMHANDLE *pCamHandleArray;  // array of CAMHANDLE allocated by caller
    int       nCameraCount;     // on entry number CAMHANDLEs allocated in 'pCamHandleArray', on exit number of detected cameras
    KYBOOL    bRetainOpenCameras;// KYTRUE if scan should skip currently active links and detect only new connections.
                                 // Open camera handles will not be affected by KYFG_CameraScanEx() call and will be retained at the same places of array where they were returned by previous call,
                                 // with exception for camera(s) that were closed between calls.
                                 // KYFALSE if all cuurently open camera handles should be reset and full re-scan should be performed.
}KYFGLib_CameraScanParameters;
#pragma pack(pop)

/// \defgroup CameraConfiguration Camera Configuration
/// @{
/**
 * \section KYFG_CameraScanEx KYFG_CameraScanEx()
 * @brief   The Frame Grabber scans for connected cameras, or performs partial re-detection depending on previously 
            defined ‘bRetainOpenCameras’ parameter, allowing to skip currently active links and detect only new connections, 
            establishing a connection and defining the default speed for each camera, on every connected channel.
            In the case of generation mode, this function is used to retrieve the number of cameras implemented by a given 
            Chameleon Simulator, and fill the array with their API handles.
 * \note Currently only one camera is implemented by Chameleon Simulator.
 * \subsection C C
 * \code{.c} FGSTATUS KYFG_CameraScanEx(FGHANDLE handle, KYFGLib_CameraScanParameters* pScanParams) \endcode
 * @param   [in] handle API handle to chosen Frame Grabber
 * @param   [in, out] pScanParams  Pointer to camera scan parameters structure
 * @return  FGSTATUS -  Status and error report. FGSTATUS_EXCEEDED_MAX_CAMERA_CONNECTIONS - value will indicate that number of connected cameras exceeds the maximum allowed connected cameras.
 * \subsection Cs C#
 * \note Not applicable.
 * \subsection Python Python
 * \code{.py} def KYFG_CameraScanEx(handle, bRetainOpenCameras) \endcode
 * \param [in] handle API handle to chosen Frame Grabber
 * \param [in] bRetainOpenCameras     TRUE- if scan should skip currently active links and detect 
                                    only new connections.
                                    FALSE- if all currently open camera handles should be reset 
                                    and full re-scan should be performed.
 * \return     FGSTATUS - Status and error report. FGSTATUS_EXCEEDED_MAX_CAMERA_CONNECTIONS - value will indicate 
            that number of connected cameras exceeds the maximum allowed connected cameras.
            Camera scan parameters structure. Type: KYFGLib_CameraScanParameters.
 * \subsection Example Example code
 * \code{.py}
        (status, ScanParameters) = KYFG_CameraScanEx(handle, True)
        print("KYFG_CameraScanEx version: ", ScanParameters.version)
        print("KYFG_CameraScanEx nCameraCount: ", ScanParameters.nCameraCount)
        print("KYFG_CameraScanEx bRetainOpenCameras: ", ScanParameters.bRetainOpenCameras)
        for i in range(0, ScanParameters.nCameraCount):
         print("KYFG_CameraScanEx pCamHandleArray: ", ScanParameters.pCamHandleArray[i])
 * \endcode
 */
KAYA_API FGSTATUS KYFG_CameraScanEx(FGHANDLE handle, KYFGLib_CameraScanParameters* pScanParams);

// Connects to a specific camera. If an xml_file_path is present then the XML is loaded from file and not from camera.
KY_DEPRECATED(KAYA_API FGSTATUS KYFG_CameraOpen(CAMHANDLE camHandle, const char *xml_file_path, const char* project_file_path), "Function 'KYFG_CameraOpen' with parameter 'project_file_path' is deprecated, please use function 'KYFG_CameraOpen2'. Project should be loaded using function 'KYFG_OpenEx'.");
/**
 * \section KYFG_CameraOpen2 KYFG_CameraOpen2()
 * \brief         Acquisition/Generation mode: Opens a connection to the chosen camera and retrieves native XML/loads built-in 
                XML file or uses external XML file provided to override the native one. The project should be loaded using the 
                function KYFG_OpenEx().
 * \subsection     C C
 * \code{.c}     FGSTATUS KYFG_CameraOpen2(CAMHANDLE camHandle, const char *xml_file_path) \endcode
 * \param [in]     camHandle API handle to the connected camera
 * \param [in]     xml_file_path Path to override XML file. If NULL, the native XML file from 
                the camera will be retrieved.
 * \return     FGSTATUS - Status and error report. INPUT_ARGUMENT_TYPE_ERROR - value will indicate that the camHandle 
            or xml_file_path are invalid.    
 * \subsection Cs C#        
 * \code{.cs} void ICamera.Open(System::String xml_file_path); \endcode
 * \param [in] xml_file_path Path to override XML file. If NULL, the native XML file from the camera will be retrieved
 * \subsection Python Python 
 * \code{.py} def KYFG_CameraOpen2(camHandle, xml_file_path) \endcode
 * \param [in] camHandle API handle to the connected camera
 * \param [in] xml_file_path Path to override XML file. If NULL, the native XML file from the 
                        camera will be retrieved. 
 * \return     FGSTATUS - Status and error report. INPUT_ARGUMENT_TYPE_ERROR - value will indicate that the camHandle 
            or xml_file_path are invalid.
 * \remarks    An XML file can be loaded to override the native XML of the camera. Otherwise, NULL should be passed to retrieve the camera’s native 
            XML file. If 'xml_file_path' is 0 then only internal XML will be used providing a minimal set of mandatory camera parameters
 
*/
KAYA_API FGSTATUS KYFG_CameraOpen2(CAMHANDLE camHandle, const char *xml_file_path);

// Closes connection to specific camera
/**
 * \section KYFG_CameraClose KYFG_CameraClose()
 * \brief     Close a connection to the selected camera. Stops data acquisition/generation and deletes previously created 
            buffers associated with the camera. The connection information is preserved, so a new connection can be 
            established later.
 * \subsection C C
 * \code{.c} FGSTATUS KYFG_CameraClose(CAMHANDLE camHandle); \endcode
 * \param camHandle [in] API handle to the connected camera
 * \return FGSTATUS - Status and error report
 * \subsection Cs C# 
 * \code{.cs} void ICamera.Close() \endcode
 * \subsection Python Python 
 * \code{.py} def KYFG_CameraClose(camHandle) \endcode
 * \param [in] camHandle API handle to the connected camera
 * \return FGSTATUS - Status and error report
 */
KAYA_API FGSTATUS KYFG_CameraClose(CAMHANDLE camHandle);
// Retrieves information about a connected camera
KY_DEPRECATED(KAYA_API FGSTATUS KYFG_CameraInfo(CAMHANDLE camHandle, KYFGCAMERA_INFO *info), "Function 'KYFG_CameraInfo' is deprecated, please use function 'KYFG_CameraInfo2'.");
/**
 * \section KYFG_CameraInfo2 KYFG_CameraInfo2()
 * \brief     Retrieves current information about the chosen camera. The camera info includes general device information and 
            connectivity topology. This function can be called before KYFG_CameraOpen2().
 * \subsection C C
 * \code{.c} FGSTATUS KYFG_CameraInfo2(CAMHANDLE camHandle, KYFGCAMERA_INFO2 *info); \endcode
 * \param [in]     camHandle API handle to the connected camera
 * \param [out]    info Pointer to camera information structure
 * \subsection Example Example code
 * \code{.c}
    CAMHANDLE camHandleArray[MAXBOARDS][KY_MAX_CAMERAS] 
    // maximum KY_MAX_CAMERAS cameras can be connected
    KYFGCAMERA_INFO2 cameraInfo;
    …
    cameraInfo.version = 1;
    KYFG_CameraInfo2(camHandleArray[grabberIndex], &cameraInfo);
    printf(“Camera model: %s, its manufacturer is %s”, 
     cameraInfo.deviceModelName, cameraInfo.deviceVendorName);
 * \endcode
 * \return FGSTATUS - Status and error report
 * \subsection Cs C#
 * \code{.cs} CAMERA_INFO ICamera.KYFG_CameraInfo(); \endcode
 * \return     Information about the chosen camera. Type: KYFGCAMERA_INFO2 
 * \subsection Python Python
 * \code{.py} def KYFG_CameraInfo2(camHandle) \endcode 
 * \param [in] camHandle API handle to the connected camera
 * \subsection Example Example code
 * \code{.py}
        (Status, camInfo) = KYFG_CameraInfo2(camHandle)
        print("version: ", str(camInfo.version))
        print("master_link: ", str(camInfo.master_link))
        print("link_mask: ", str(camInfo.link_mask))
        print("link_speed: ", str(camInfo.link_speed))
        print("stream_id: ", str(camInfo.stream_id))
        print("deviceVersion: ", str(camInfo.deviceVersion))
        print("deviceVendorName: ", str(camInfo.deviceVendorName))
        print("deviceManufacturerInfo: ", str(camInfo.deviceManufacturerInfo))
        print("deviceModelName: ", str(camInfo.deviceModelName))
        print("deviceID: ", str(camInfo.deviceID))
        print("deviceUserID: ", str(camInfo.deviceUserID))
        print("outputCamera: ", str(camInfo.outputCamera))
        print("virtualCamera: ", str(camInfo.virtualCamera))
 * \endcode
 * \return FGSTATUS - Status and error report <br>Information about the chosen camera. Type: KYFGCAMERA_INFO2 
 * \remarks 1. Users who want this member to be considered by KYFG_CameraInfo2 call, must set the version value to 0.

*/
KAYA_API FGSTATUS KYFG_CameraInfo2(CAMHANDLE camHandle, KYFGCAMERA_INFO2 *info); 

/**
 * \section KYFG_CameraStart KYFG_CameraStart()
 * @brief    <b>Acquisition mode:</b> Starts stream acquisition for the specified stream and camera. A single stream can be active 
                at a time for each camera. The buffers of the selected stream will be filled with data acquired from the camera. 
                The total number of acquired frames can be limited by the <frames> parameter; 0 value will indicate a continuous 
                frame acquisition.<br>
            <b>Generation mode:</b> Starts stream generation for the specified stream and camera. The number of frames to be 
                generated will be defined by the <frames> parameter; 0 value will result in continuous frame generation.
 * \remarks Starting from API 5.2 this function will return FGSTATUS_STREAM_CANNOT_LOCK if an acquisition is in process at the time of calling 
            this function. The application must stop the previous acquisition before starting a new one.
 * \subsection C C
 * \cade{.c} FGSTATUS KYFG_CameraStart(CAMHANDLE camHandle, STREAM_HANDLE streamHandle, int frames); \endcode
 * @param   [in] camHandle API handle to the connected camera
 * @param   [in] streamHandle API handle to the data stream for selected camera
 * @param   [in] frames The number of frames to be acquired/ generated, 0 for continuous acquisition mode.
 * @return  FGSTATUS - Status and error report
 * \subsection Cs C#
 * \code{.cs} void ICamera.Start(IStream streamHandle, int frames); \endcode
 * \param [in] streamHandle API handle to data stream for selected camera
 * \param [in] frames The number of frames to be acquired/ generated, 0 for continuous acquisition mode
 * \subsection Python Python
 * \code{.py} def KYFG_CameraStart(camHandle, streamHandle, frames) \endcode
 * \param [in] camHandle API handle to connected camera
 * \param [in] streamHandle API handle to data stream for selected camera
 * \param [in] frames The number of frames to be acquired/ generated, 0 for continuous acquisition mode
 * \return FGSTATUS - Status and error report.
 */
KAYA_API FGSTATUS KYFG_CameraStart(CAMHANDLE camHandle, STREAM_HANDLE streamHandle, uint32_t frames);
/// @}
KY_DEPRECATED2(KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_CameraStartAcquire(CAMHANDLE camHandle, STREAM_HANDLE streamHandle, uint32_t frames) KY_FORCE_INLINE_GCC, "Function 'KYFG_CameraStartAcquire' is deprecated, please use function 'KYFG_CameraStart'.");
KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_CameraStartAcquire(CAMHANDLE camHandle, STREAM_HANDLE streamHandle, uint32_t frames)
{
    return KYFG_CameraStart(camHandle, streamHandle, frames);
}
/// \defgroup CameraConfiguration Camera Configuration
/// @{
/**
 * \section KYFG_CameraStop KYFG_CameraStop()
 * \brief     <b>Acquisition mode:</b> Stops stream acquisition of the active stream for the selected camera.
            <b>Generation mode:</b> Stops stream generation of the active stream for the selected camera.
 * \subsection C C
 * \code{.c} FGSTATUS KYFG_CameraStop(CAMHANDLE camHandle); \endcode
 * \param   [in] camHandle API handle to the connected camera
 * \return  FGSTATUS - Status and error report.
 * \subsection Cs C#
 * \code{.cs}void ICamera.Stop();\endcode
 * \subsection Python Python 
 * \code{.py} def KYFG_CameraStop(camHandle) \endcode
 * \param [in] camHandle API handle to connected camera
 * \return FGSTATUS - Status and error report.
 */
KAYA_API FGSTATUS KYFG_CameraStop(CAMHANDLE camHandle);
/// @}
KY_DEPRECATED2(KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_CameraStopAcquire(CAMHANDLE camHandle) KY_FORCE_INLINE_GCC, "Function 'KYFG_CameraStopAcquire' is deprecated, please use function 'KYFG_CameraStop'.");
KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_CameraStopAcquire(CAMHANDLE camHandle)
{
    return KYFG_CameraStop(camHandle);
}
/// \defgroup CameraConfiguration Camera Configuration
/// @{
/**
 * \section KYFG_CameraGetXML KYFG_CameraGetXML()
 * @brief      Extracts native XML file from the chosen camera and fills user allocated buffer. The size (in bytes) and file type 
            (.xml or .zip) are also retrieved even if the buffer isn’t large enough to hold all file data
 * \subsection C C 
 * @param   [in] camHandle     -     API handle to the connected camera
 * @param   [out] buffer     -    Pointer to user allocated buffer
 * @param   [out] isZipFile -     Pointer to indicator whether the camera’s XML file is in ZIP or 
                                XML format
 * @param   [out] bufferSize - Pointer to the size of the buffer. 
 * @return  FGSTATUS -    Status and error report. FGSTATUS_BUFFER_TOO_SMALL – value will indicate that provided buffer 
                        size is too small to hold the file to be extracted
 * \remarks     1.    On entry, bufferSize [in] should be set to the size of the allocated buffer. After the function returns, bufferSize [out] will hold the actual 
                    size of the content extracted.
 * \remarks     2.     The bufferSize of the ZIP file will indicate the required buffer size and type of camera XML file. If the value is smaller than the needed 
                    size, or the buffer value is NULL, the buffer will not be filled
 * \subsection Example Example code
 * \code{.c}
        CAMHANDLE camHandleArray[MAXBOARDS][KY_MAX_CAMERAS] 
        // maximum KY_MAX_CAMERAS cameras can be connected
        char* buffer;
        uint64_t bufferSize = 0;
        KYBOOL isZip = KYFALSE;
        FILE* fileOut = NULL;
        // Get the size of the buffer to allocate.
        bufferSize = 0;
        if(FGSTATUS_BUFFER_TOO_SMALL== KYFG_CameraGetXML(camHandleArray[grabberIndex], NULL, 
        &isZip, &bufferSize))
        {
        buffer = (char*)malloc(bufferSize); // allocate memory for buffer
        // extract camera’s native XML file
        if(FGSTATUS_OK == KYFG_CameraGetXML(camHandleArray[grabberIndex], buffer, 
         &isZip, &bufferSize)) 
        {
        if(KYTRUE == isZip)
         fileOut = fopen("camera_xml.zip","wb"); // camera XML file in zip format
         else
         fileOut = fopen("camera_xml.xml","wb"); // camera XML file in xml format
         if (NULL != fileOut)
         {
         fwrite(buffer, bufferSize, 1, fileOut);
         fclose(fileOut);
        }
        }
        free(buffer); // free buffer after use
        }
 * \endcode
 * \subsection Cs C#
 * \code{.cs} System.Tuple<byte[], KYBOOL> ICamera.GetXML(); \endcode
 * \return Tuple(xml_managed_string, isZip_managed), where: <br>1. xml_managed_string - Byte array, which contains the required XML <br>2.  isZip_managed - KY_TRUE if the XML archived within Zip, KY_FALSE otherwise.
 * \subsection Example Example code
 * \code{.cs}
        Tuple<byte[], KAYA.KYBOOL> xml_string_tuple = camera.GetXML();
        if (xml_string_tuple.Item2 == KAYA.KYBOOL.KY_TRUE)
        {
        System.IO.File.WriteAllBytes("C:\\Users\\PC-01\\Desktop\\cameras_xml.zip", 
         xml_string_tuple.Item1);
        }
        else
        {
        System.IO.File.WriteAllBytes("C:\\Users\\PC-01\\Desktop\\ cameras_xml.xml",
         xml_string_tuple.Item1);
        }
 * \endcode    
 * \subsection Python Python
 * \code{.py} def KYFG_CameraGetXML(camHandle) \endcode
 * \param camHandle API handle to the connected camera.
 * \return FGSTATUS - Status and error report. <br>isZipFile - Indicator whether the camera’s XML file is in ZIP or XML format. Type: KYBOOL<br>buffer – Bytearray, that includes xml string or zip binary file
 * \subsection Example Example code
 * \code{.py}
        (KYFG_CameraGetXML_status, isZipped, buffer) = 
        KYFG_CameraGetXML(camHandleArray[grabberIndex][0])
        print("Is Zipped: " + str(isZipped.get()))
        print("KYFG_CameraGetXML_status: " + str(format(KYFG_CameraGetXML_status, '02x')))
        if (isZipped == False):
        print("Writing buffer to xml file...")
        newFile = open("camera_xml.xml","w")
        newFile.write(''.join(buffer))
        newFile.close()
        else:
         print("Writing buffer to zip file...")
         newFile = open("camera_xml.zip","wb")
         newFile.write(bytes(buffer))
         newFile.close()
 * \endcode
*/
KAYA_API FGSTATUS KYFG_CameraGetXML(CAMHANDLE camHandle, char *buffer, KYBOOL* isZipFile, uint64_t *bufferSize);
/// @}

KY_DEPRECATED(KAYA_API FGSTATUS KYFG_GetXML(CAMHANDLE camHandle, char** buffer, uint64_t *bufferSize, KYBOOL *isZipFile), "Function 'KYFG_GetXML' is deprecated, please use function 'KYFG_CameraGetXML'.");

/**
 * \page IOConfigurations IO Configurations
 * \section IOSelectors IO Selectors
 * KAYA’s Frame Grabbers provide a vast variety of configurable I/Os. This depends on the device’s hardware, 
    firmware and software capabilities:
 * - 4 Encoders
 * - Per-channel Frame Grabber Triggers and Camera Triggers
 * - 8 Timers
 * - IO Outputs
 * - IO Inputs
 * Parameter naming is according to “Gen<i>Cam Standard Features Naming Convention version 2.1”. I/Os are 
    configurable via Gen<i>Cam interface using selectors to specify the specific I/O to be configured. To configure the 
    specific I/O feature follow these steps:
 * 1. Set selector value of specific group (Timer, Stream Trigger, Camera Triggers, Encoder or GPIO) of the required 
    item to be configured.
 * 2. Set the selected I/O properties to the required value
 * In order to configure the selector and values use KYFG_SetGrabberValue() and KYFG_GetGrabberValue() (or one of the provided sub-functions).
 * \section ConfigurationFields Configuration fields
 * Complete available triggers list and possible configurations can be found in the “KAYA Frame Grabber Feature 
    Guide” document
 * \remarks For I/O source options of Firmware Version 1.xx, follow tables no. 4 and 5 located in the Appendices section
 * \subsection Example Example code
 * This example shows how to set the Timer 2 trigger source to be in continuous mode:
 * \code{.c}
    FGHANDLE handle; // maximum 4 cameras can be connected
    int64_t timerSelectorValue = 2;
    // select the timer to configure
    KYFG_SetGrabberValueEnum(handle,"TimerSelector", timerSelectorValue);
    // select the timer trigger source configuration
    KYFG_SetGrabberValueEnum_ByValueName(handle, "TimerTriggerSource", "KY_CONTINUOUS");
 * \endcode
 * The following example illustrates how to configure TTL 0 to be an output signal source generating a square wave 
    using connected Timer 0 at 10Hz frequency (every 100ms). Also configuring TTL 1 to be an input for incoming 
    signal, which then will become the source of camera trigger over the coax master channel of the camera. To 
    complete the setup TTL 0 is connected to TTL 1 to pass the signal from Timer 0 to the camera over coax. 
    This setup both generates a trail of trigger packets to the camera and a way to connect an oscilloscope, for example, to TTL 0 and sample the generated signals.
 * \code{.c}
    // Setup:
    // 1) Connect oscilloscope to TTL 0 pin 
    // 2) Connect TTL 0 to TTL 1 pin
    // 3) TTL 1 input will be sending triggers to camera over coax
    
    
    FGHANDLE handle;
    // 1) Configure timer 0 to create square wave
    // select the timer trigger source configuration
    KYFG_SetGrabberValueEnum_ByValueName(handle,"TimerSelector", "Timer0" ); 
    // set continues mode
    KYFG_SetGrabberValueEnum_ByValueName(handle, "TimerTriggerSource", "KY_CONTINUOUS"); 
    // example with 10Hz:
    KYFG_SetGrabberValueFloat(handle, "TimerDelay", 50000.0); // 50ms
    KYFG_SetGrabberValueFloat(handle, "TimerDuration", 50000.0); // 50ms
    // example with 10KHz: 
    // KYFG_SetGrabberValueFloat(handle, "TimerDelay", 50.0); // 50us
    // KYFG_SetGrabberValueFloat(handle, "TimerDuration", 50.0); // 50us
    // explanation:
    // 50,000us Delay + 50,000us Duration = 100ms total clock pulse = 10Hz
    // 50us Delay + 50us Duration = 100us total clock pulse = 10KHz
    // >------------------------------------------------------------------------------------------------------------------------<
    // 2) Setting timer to be source of TTL 0
    // select the TTL 0 settings
    KYFG_SetGrabberValueEnum_ByValueName(handle, "LineSelector", "KY_TTL_0"); 
    // set TTL 0 direction to output
    KYFG_SetGrabberValueEnum_ByValueName(handle, "LineMode", "Output"); 
    // select TTL 0 source as timer 0
    KYFG_SetGrabberValueEnum_ByValueName(handle, "LineSource", "KY_TIMER_ACTIVE_0"); 
    // >------------------------------------------------------------------------------------------------------------------------<
    // 3) Configuring TTL 1 to be input
    // select TTL 1 settings
    KYFG_SetGrabberValueEnum_ByValueName(handle, "LineSelector", "KY_TTL_1"); 
    // set TTL 1 direction to input
    KYFG_SetGrabberValueEnum_ByValueName(handle, "LineMode", "Input");
    // disable TTL 1 source
    KYFG_SetGrabberValueEnum_ByValueName(handle, "LineSource", "KY_DISABLED");
    // >------------------------------------------------------------------------------------------------------------------------<
    // 4) Setting TTL 1 to be trigger for camera
    // select the camera trigger settings
    KYFG_SetGrabberValueEnum(handle, "CameraSelector", 0); // select the camera to work with
    // enable camera trigger
    KYFG_SetGrabberValueEnum_ByValueName(handle, "CameraTriggerMode", "On"); 
    // select camera trigger source as TTL 1
    KYFG_SetGrabberValueEnum_ByValueName(handle, "CameraTriggerSource", "KY_TTL_1");

 * \endcode




*/


// <-- Genicam functions (requires connection of GeniCam SDK) -->
// Set device value using Gen<i>Cam
KAYA_API FGSTATUS   KYFG_SetGrabberValue(FGHANDLE handle, const char *paramName, const void *paramValue);

KAYA_API FGSTATUS KYFG_SetGrabberValueInt(FGHANDLE handle, const char *paramName, int64_t value);

KAYA_API FGSTATUS   KYFG_SetGrabberValueFloat(FGHANDLE handle, const char *paramName, double value);

KAYA_API FGSTATUS   KYFG_SetGrabberValueBool(FGHANDLE handle, const char *paramName, KYBOOL value);

KAYA_API FGSTATUS   KYFG_SetGrabberValueEnum(FGHANDLE handle, const char *paramName, int64_t value);

KAYA_API FGSTATUS   KYFG_SetGrabberValueString(FGHANDLE handle, const char *paramName, const char* value);

KY_DEPRECATED2( KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS KYFG_ExecuteGrabberCommand(FGHANDLE handle, const char *paramName) KY_FORCE_INLINE_GCC, "Function 'KYFG_ExecuteGrabberCommand' is deprecated, please use function 'KYFG_GrabberExecuteCommand(FGHANDLE handle, const char *paramName)'.");
KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS   KYFG_ExecuteGrabberCommand(FGHANDLE handle, const char *paramName)
{
    int64_t dummyValue = 1;
    return KYFG_SetGrabberValue(handle, paramName, &dummyValue);
}
KAYA_API FGSTATUS   KYFG_GrabberExecuteCommand(FGHANDLE handle, const char *paramName);
KAYA_API FGSTATUS    KYFG_SetGrabberValueEnum_ByValueName(FGHANDLE handle, const char *paramName, const char *paramValueName);

// Set specific camera value using Gen<i>Cam 
KAYA_API FGSTATUS KYFG_SetCameraValue(CAMHANDLE camHandle, const char *paramName, const void *paramValue);

KAYA_API FGSTATUS KYFG_SetCameraValueInt(CAMHANDLE camHandle, const char *paramName, int64_t value);

KAYA_API FGSTATUS KYFG_SetCameraValueFloat(CAMHANDLE camHandle, const char *paramName, double value);

KAYA_API FGSTATUS KYFG_SetCameraValueBool(CAMHANDLE camHandle, const char *paramName, KYBOOL value);

KAYA_API FGSTATUS KYFG_SetCameraValueEnum(CAMHANDLE camHandle, const char *paramName, int64_t value);


KAYA_API FGSTATUS KYFG_SetCameraValueString(CAMHANDLE camHandle, const char *paramName, const char* value);


KY_DEPRECATED2(KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS   KYFG_ExecuteCommand(CAMHANDLE camHandle, const char *paramName) KY_FORCE_INLINE_GCC, "Function 'KYFG_ExecuteCommand' is deprecated, please use function 'KYFG_GrabberExecuteCommand or KYFG_CameraExecuteCommand'.");
KY_INLINE KY_FORCE_INLINE_MSVC KAYA_API FGSTATUS   KYFG_ExecuteCommand(CAMHANDLE camHandle, const char *paramName)
{
    int64_t dummyValue = 1;
    return KYFG_SetCameraValue(camHandle, paramName, &dummyValue);
}
KAYA_API FGSTATUS   KYFG_CameraExecuteCommand(CAMHANDLE camHandle, const char *paramName);
// Sets Enumeration camera value by field name rather then its numeric value
KAYA_API FGSTATUS    KYFG_SetCameraValueEnum_ByValueName(CAMHANDLE camHandle, const char *paramName, const char *paramValueName);

#ifdef __cplusplus
static const int64_t INVALID_INT_PARAMETER_VALUE = INT64_MAX;
static const double INVALID_FLOAT_PARAMETER_VALUE = LDBL_MAX;
#else
#define INVALID_INT_PARAMETER_VALUE INT64_MAX
#define INVALID_FLOAT_PARAMETER_VALUE LDBL_MAX
#endif

// Get Frame Grabber value using Gen<i>Cam
KAYA_API FGSTATUS   KYFG_GetGrabberValue(FGHANDLE handle, const char *paramName, void *paramValue);

KAYA_API FGSTATUS   KYFG_GetGrabberValueIntMaxMin(FGHANDLE handle, const char *paramName, int64_t* pIntMax, int64_t* pIntMin);
KAYA_API FGSTATUS   KYFG_GetGrabberValueFloatMaxMin(FGHANDLE handle, const char *paramName, double* pDoubleMax, double* pDoubleMin);

KAYA_API int64_t KYFG_GetGrabberValueInt(FGHANDLE handle, const char *paramName);

KAYA_API int64_t KYFG_GetGrabberValueEnum(FGHANDLE handle, const char *paramName);

KAYA_API double KYFG_GetGrabberValueFloat(FGHANDLE handle, const char *paramName);

KAYA_API KYBOOL KYFG_GetGrabberValueBool(FGHANDLE handle, const char *paramName);


// Get Frame Grabber Parameter type
KAYA_API KY_CAM_PROPERTY_TYPE KYFG_GetGrabberValueType(FGHANDLE handle, const char *paramName);
KAYA_API KYBOOL KYFG_IsGrabberValueImplemented(FGHANDLE handle, const char *paramName);

// This function allocates memory for the string. The caller is responsible for releasing this memory
KY_DEPRECATED(KAYA_API FGSTATUS KYFG_GetGrabberValueString(FGHANDLE handle, const char *paramName, char** ptr), "Function 'KYFG_GetGrabberValueString' is deprecated, please use function 'KYFG_GetGrabberValueStringCopy' to retrieve string conent");

KAYA_API FGSTATUS KYFG_GetGrabberValueStringCopy(FGHANDLE handle, const char *paramName, char *stringPtr, uint32_t *stringSize);

KAYA_API FGSTATUS KYFG_GetGrabberValueRegister(FGHANDLE handle, const char *paramName, uint8_t *bufferPtr, uint32_t *bufferSize);

// Get camera Parameter type
KAYA_API KY_CAM_PROPERTY_TYPE KYFG_GetCameraValueType(CAMHANDLE camHandle, const char *paramName);
KAYA_API KYBOOL KYFG_IsCameraValueImplemented(CAMHANDLE camHandle, const char *paramName);

// This function allocates memory for the string. The caller is responsible for releasing this memory
KY_DEPRECATED(KAYA_API FGSTATUS KYFG_GetCameraValueString(CAMHANDLE camHandle, const char *paramName, char** ptr), "Function 'KYFG_GetCameraValueString' is deprecated, please use function 'KYFG_GetCameraValueStringCopy' to retrieve string conent");

KAYA_API FGSTATUS KYFG_GetCameraValueStringCopy(CAMHANDLE camHandle, const char *paramName, char *stringPtr, uint32_t *stringSize);


// Get specific camera value using Gen<i>Cam
KAYA_API FGSTATUS KYFG_GetCameraValue(CAMHANDLE camHandle, const char *paramName, void *paramValue);

KAYA_API FGSTATUS   KYFG_GetCameraValueIntMaxMin(CAMHANDLE handle, const char *paramName, int64_t* pIntMax, int64_t* pIntMin);
KAYA_API FGSTATUS   KYFG_GetCameraValueFloatMaxMin(CAMHANDLE handle, const char *paramName, double* pDoubleMax, double* pDoubleMin);

KAYA_API int64_t KYFG_GetCameraValueInt(CAMHANDLE camHandle, const char *paramName);

KAYA_API int64_t KYFG_GetCameraValueEnum(CAMHANDLE camHandle, const char *paramName);

KAYA_API double KYFG_GetCameraValueFloat(CAMHANDLE camHandle, const char *paramName);


KAYA_API KYBOOL KYFG_GetCameraValueBool(CAMHANDLE camHandle, const char *paramName);

KAYA_API FGSTATUS KYFG_GetCameraValueRegister(CAMHANDLE camHandle, const char *paramName, uint8_t *bufferPtr, uint32_t *bufferSize);

// Camera configuration
KAYA_API FGSTATUS KYFG_LoadCameraConfiguration(CAMHANDLE camHandle, const char *xmlFilePath, const char* valuesFilePath);
// TODO: Implement sugar for:
// KYCS_API CSSTATUS KYCS_LoadCameraConfiguration(CSHANDLE handle, char *defFile, char* valuesFile);

#ifdef __cplusplus
static const int KY_AUTHKEY_SIZE = 32;
#else
#define KY_AUTHKEY_SIZE 32
#endif
typedef struct _KY_AuthKey 
{ 
    unsigned char secret[KY_AUTHKEY_SIZE]; 
} KY_AuthKey;
KAYA_API FGSTATUS KYFG_CALLCONV KY_AuthProgramKey(FGHANDLE handle, KY_AuthKey* pKey, int lock);
KAYA_API FGSTATUS KYFG_CALLCONV KY_AuthVerify(FGHANDLE handle, KY_AuthKey* pKey);

KAYA_API FGSTATUS KYFG_LoadFileData(STREAM_HANDLE streamHandle, const char* path, const char* type, int frames);
KAYA_API FGSTATUS KYFG_LoadPatternData(STREAM_HANDLE streamHandle, PATTERN_TYPE type, uint16_t *FixedPatternColor);
/////////////////////////////////////////////////////////////////////////////
//
// Firmware update
//
/////////////////////////////////////////////////////////////////////////////
/**
 * \page Structures Structures
 * \section UPDATE_STATUS UPDATE_STATUS
 * | Structure Field          | Type                     | Description     |
 * | ------------------     | -----------------     | ---------        |
 * | struct_version         | int                   | Currently code initializes this with "1". |
 * | bytes_sent             | uint64_t              | bytes already sent |
 * | total_size             | uint64_t              | firmware file size |
 * | KYBOOL                 | is_writing            | true - writing new firmware; false    - validating new firmware |
 */
#pragma pack(push, 1)
typedef struct _update_status
{
    int      struct_version; // Currently code initializes this with "1". 
                             // If more fields will be added to this stuct in future code will be changed and initialize it with "2", etc.
                             // See functions 'alt_epcs_flash_validate' and 'alt_epcs_flash_write' where this field is initialized

    // Fileds of initial 1-st version of this struct:
    uint64_t    bytes_sent; // bytes already sent
    uint64_t    total_size; // firmware file size
    KYBOOL      is_writing; // true - writing new firmware
                            // false    - validating new firmware

    // Fields added in 2nd version of this struct:

}UPDATE_STATUS;
#pragma pack(pop)

/**
 * @page APIDefinesAndMacros
 * @section UPDATE_CALLBACK UPDATE_CALLBACK
 * Runtime firmware update callback function prototype. Callbacks are issued during firmware updates to report progress
 * \code{.c} typedef KYBOOL(*UPDATE_CALLBACK)(const UPDATE_STATUS* UpdateStatus, void* context); \endcode
*/
typedef KYBOOL(*UPDATE_CALLBACK)(const UPDATE_STATUS* UpdateStatus, void* context);
/**  
 @defgroup FirmwareUpdate Firmware update
 @{
  For using functions @ref KYFG_LoadFirmware and @ref KYFG_GetFirmareUpdateFileInfo you can find KYFGLib_Example_Firmware_update example in API samples folder
*/
/**

 * @section KYFG_LoadFirmware KYFG_LoadFirmware()
 * Updates device firmware from the supplied binary file. Progress is reported via a supplied callback function
 * @subsection C C
 * @code{.c} FGSTATUS KYFG_LoadFirmware(FGHANDLE handle, const char* file, UPDATE_CALLBACK callback, void* context); @endcode
 * @param [in] handle API handle to chosen Video Processor
 * @param [in] file The file name for firmware update
 * @param [in] callback Pointer to \ref UPDATE_CALLBACK callback function to be called during an update
 * @param [in] context Pointer to user context. This pointer will be passed the callback function. 
 * @return \ref FGSTATUS Status and error report
 * @subsection Cs C#
 * Not applicable
 * @subsection Python Python
 * @code{.py} def KYFG_LoadFirmware(handle, file, callbackFunc, context): @endcode
 * @param handle API handle to chosen Video Processor
 * @param file The file name for firmware update
 * @param callbackFunc callback function to be called during an update
 * @param context Pointer to user context. This pointer will be passed the callback function. 
 * @return \ref FGSTATUS Status and error report
 */
KAYA_API FGSTATUS KYFG_LoadFirmware(FGHANDLE handle, const char* file, UPDATE_CALLBACK callback, void* context);

/**
 * \page APIDefinesAndMacros
 * \section KYFG_UPDATE_FILE KYFG_UPDATE_FILE
 * | Structure Field          | Type                     | Description     |
 * | ------------------     | -----------------     | ---------        |
 * | version;               | uint16_t              | Struct version shoud be set to 1 by user |
 * | FlashMinorRev;         | uint16_t              | uint16_t that will be filled with the minor revision number of the current firmware         |
 * | FlashMajorRev;         | uint16_t              | uint16_t that will be filled with the major revision number of the current firmware         |
 * | FileMinorRev;          | uint16_t              | uint16_t that will be filled with the minor revision number of the firmware contained in the update file         |
 * | FileMajorRev;          | uint16_t              | uint16_t that will be filled with the major revision number of the firmware contained in the file         |
 * | FlashVendorId;         | uint16_t              | uint16_t that will be filled with the vendor ID of the current firmware         |
 * | FlashBoardId;          | uint16_t              | uint16_t that will be filled with board ID of the current firmware         |
 * | FileVendorId;          | uint16_t              | uint16_t that will be filled with the vendor ID of firmware contained in the file         |
 * | FileBoardId;           | uint16_t              | uint32_t that will be filled with board ID of firmware contained in the file         |
 * | FlashVersion;          | uint32_t              | uint32_t that will be filled with Flash version path         |
 * | FlashTimeStamp;        | uint32_t              | uint32_t that will be filled with flash timestamp         |
 * | FileVersion;           | uint32_t              | uint32_t that will be filled with version path         |
 * | FileTimeStamp;         | uint32_t              | uint32_t that will be filled with file timestamp         |
 */
#pragma pack(push, 1)
typedef struct _KYFGUpdateFile
{
    uint16_t version;
    uint16_t flashMinorRev;
    uint16_t flashMajorRev;
    uint16_t fileMinorRev;
    uint16_t fileMajorRev;
    uint16_t flashVendorId;
    uint16_t flashBoardId;
    uint16_t fileVendorId;
    uint16_t fileBoardId;
    uint32_t flashVersion;
    uint32_t flashTimeStamp;
    uint32_t fileVersion;
    uint32_t fileTimeStamp;

} KYFG_UPDATE_FILE;
#pragma pack(pop)
/**
 * @section KYFG_GetFirmareUpdateFileInfo KYFG_GetFirmareUpdateFileInfo()
 * Retrieves information about firmware contained in the supplied binary file and the current firmware of the card.
 * @subsection C C
 * @code{.c} FGSTATUS KYFG_GetFirmareUpdateFileInfo(FGHANDLE handle, const char* file, KYFG_UPDATE_FILE updateFileStruct); @endcode
 * @param [in] handle API handle to chosen Video Processor
 * @param [in] file Full path to a firmware update file. If file is NULL then all "file" members of the \ref KYFG_UPDATE_FILE struct will be nullified
 * @param [out] UpdateFile struct  Pointer \ref KYFG_UPDATE_FILE struct. Struct version should be set by user
 * @return \ref FGSTATUS Status and error report
 * @subsection Cs C#
 * Not applicable
 * @subsection Python Python
 * @code{.py} def KYFG_GetFirmareUpdateFileInfo(handle, file_path) @endcode
 * @param handle handle API handle to chosen Video Processor
 * @param file Full path to a firmware update file. If file is NULL then all "file" members of the \ref KYFG_UPDATE_FILE struct will be nullified
 * @return \ref FGSTATUS Status and error report
 * @return KYFG_CHECK_UPDATE_FILE information about update file
 */

KAYA_API FGSTATUS KYFG_GetFirmareUpdateFileInfo(FGHANDLE handle, const char* file, KYFG_UPDATE_FILE* updateFile);
/// @}


/////////////////////////////////////////////////////////////////////////////
//
// Chameleon interface provided for backward compatibility:
//
/////////////////////////////////////////////////////////////////////////////

typedef FGSTATUS CSSTATUS;
typedef FGHANDLE CSHANDLE;

KAYA_API CSSTATUS KYCS_ReadBootstrapRegs(FGHANDLE handle, uint32_t address, void* data, uint32_t size);
KAYA_API CSSTATUS KYCS_WriteBootstrapRegs(FGHANDLE handle, uint32_t address, const void* data, uint32_t size);


//
// Image header access
//

// Maximum size that can be specified for KYCS_InjectImageHeader() and KYCS_GetImageHeader().
static const unsigned int IMAGE_HEADER_SIZE = 32; 

/**
 * @brief   Retrieves current image generation header
 * @param   [in] handle - handle to the camera simulator device
 * @param   [out] header - user supplied buffer where header should be stored
 * @param   [in] size - size of user supplied buffer. Maximum IMAGE_HEADER_SIZE can be specified, otherwise CSSTATUS_IMAGE_HEADER_INJECTION_SIZE_TOO_BIG will be returned by function
 * @return  CSSTATUS - Error code of function execution status
 */
KAYA_API CSSTATUS    KYCS_GetImageHeader(CSHANDLE handle, unsigned char* header , unsigned int size);

/**
 * @brief   Allow to overwrite one field of the standard stream image during a configurable number of image generation cycles.
 * @param   [in] handle - handle to camera the simulator device
 * @param   [in] header - user supplied buffer with header 
 * @param   [in] size - size of user supplied buffer. Maximum IMAGE_HEADER_SIZE can be specified, otherwise CSSTATUS_IMAGE_HEADER_INJECTION_SIZE_TOO_BIG will be returned by function
 * @param   [in] cycles - number of image generation cycles. Any value less than 1 is treated as -1 and stops further injections
 * @return  CSSTATUS - Error code of function execution status
 */
KAYA_API CSSTATUS    KYCS_InjectImageHeader(CSHANDLE handle, unsigned char* header , unsigned int size, int cycles);

//
// CRC Injection
//

/**
 * @brief   Allow to set a wrong CRC in one stream packet of one image generation, during a configurable number of image generation cycles.
 * @param   [in] handle - handle to the camera simulator device
 * @param   [in] cycles - number of image generation cycles. Any value less than 1 is treated as -1 and stops further injections
 * @return  CSSTATUS - Error code of function execution status
 */
KAYA_API CSSTATUS    KYCS_InjectVideoCRCErrors(CSHANDLE handle, int cycles);

/**
 * @brief   Allow to set a wrong CRC in the next return Control/Command packets, during a configurable number of cycles.
 * @param   [in] handle - handle to the camera simulator device
 * @param   [in] cycles - number of image generation cycles. Any value less than 1 is treated as -1 and stops further injections
 * @return  CSSTATUS - Error code of function execution status
 */
KAYA_API CSSTATUS    KYCS_InjectControlCRCErrors(CSHANDLE handle, int cycles);

//
// IO Control
//
KAYA_API CSSTATUS    KYCS_SetTriggerSource(CSHANDLE handle, KY_IO source);
KAYA_API CSSTATUS    KYCS_ConfigureIO(CSHANDLE handle, KY_IO source, KY_IO sink, KYBOOL Invert);
KAYA_API CSSTATUS    KYCS_SetOutputEnable(CSHANDLE handle, KY_IO sink);


KAYA_API CSSTATUS    KYCS_GenerateCxpEvent(CSHANDLE handle, KY_CXPEVENT_PACK* pEvent);





// UNDER DEVELOPMENT:
// DO NOT USE CODE BELOW IN YOUR PROGRAM!!!
typedef enum _KY_NodeDescriptorType
{
    KY_NodeDescriptorType_Invalid,
    KY_NodeDescriptorType_NewNode,
    KY_NodeDescriptorType_NewEnumEntry,
    KY_NodeDescriptorType_UpdateNode
}KY_NodeDescriptorType;

typedef enum _KY_ParameterInterfaceType
{
    KY_ParameterInterfaceType_IValue,
    KY_ParameterInterfaceType_IBase,
    KY_ParameterInterfaceType_IInteger,
    KY_ParameterInterfaceType_IBoolean,
    KY_ParameterInterfaceType_ICommand,
    KY_ParameterInterfaceType_IFloat,
    KY_ParameterInterfaceType_IString,
    KY_ParameterInterfaceType_IRegister,
    KY_ParameterInterfaceType_ICategory,
    KY_ParameterInterfaceType_IEnumeration,
    KY_ParameterInterfaceType_IEnumEntry,
    KY_ParameterInterfaceType_IPort,
    KY_ParameterInterfaceType_IUnknown
}KY_ParameterInterfaceType;

typedef enum _KY_ParameterRepresentation
{
    KY_ParameterRepresentation_Linear,
    KY_ParameterRepresentation_Logarithmic,
    KY_ParameterRepresentation_Boolean,
    KY_ParameterRepresentation_PureNumber,
    KY_ParameterRepresentation_HexNumber,
    KY_ParameterRepresentation_IPV4Address,
    KY_ParameterRepresentation_MACAddress,
    KY_ParameterRepresentation_UndefinedRepresentation
}KY_ParameterRepresentation;

typedef enum _KY_ParameterVisibility
{
    KY_ParameterVisibility_Unknown = 0xFF,
    KY_ParameterVisibility_Zero = 0x00,
    KY_ParameterVisibility_Beginner = 0x01,
    KY_ParameterVisibility_Expert = 0x02,
    KY_ParameterVisibility_Guru = 0x03,
    KY_ParameterVisibility_Developer = 0x04,
    KY_ParameterVisibility_Invisible = 0x05

}KY_ParameterVisibility;

#pragma pack(push, 1)
struct KY_PARAMETER_DESCRIPTOR
{
    int version;

    KY_NodeDescriptorType descriptorType;
    KY_ParameterInterfaceType interfaceType;
    const char* paramName;
    const char* paramDisplayName;
    const char* toolTip;
    KYBOOL isAvailable;
    KYBOOL isWritable;
    KYBOOL isReadable;        
    KYBOOL isSelector;

    KY_ParameterRepresentation representation;
    KY_ParameterVisibility visibility;

    int64_t minIntValue;
    int64_t maxIntValue;
    int64_t incIntValue;

    int64_t curIntValue;
    double minFloatValue;
    double maxFloatValue;
    double incFloatValue;

    int64_t  floatDisplayPrecision;
    double curFloatValue;
    KYBOOL   curBoolValue;
    const char* curStringValue;

    const struct KY_PARAMETER_DESCRIPTOR* pParentNode;

    const char* selectorName;
    int64_t curSelectorValue;
    const char* description;

    KYBOOL bTracing;

    const char* physicalUnitName;
};
#pragma pack(pop)

KAYA_API FGSTATUS KY_GetGrabberPropertyParameterValue(FGHANDLE grabberHandle, const char *paramName, const char *propertyName, char *propertyValue, uint32_t *stringSize);

KAYA_API FGSTATUS KY_GetCameraPropertyParameterValue(CAMHANDLE camHandle, const char *paramName, const char *propertyName, char *propertyValue, uint32_t *stringSize);

typedef void (KYFG_CALLCONV *KY_ParameterCallback)(void* userContext, const struct KY_PARAMETER_DESCRIPTOR* pParameterDescriptor, int grouppingLevel);

KAYA_API FGSTATUS KY_RegisterGrabberConfigurationParameterCallback(FGHANDLE grabberHandle, KY_ParameterCallback userFunc, void* userContext);

KAYA_API FGSTATUS KY_UnregisterGrabberConfigurationParameterCallback(FGHANDLE grabberHandle, KY_ParameterCallback userFunc);

KAYA_API FGSTATUS KY_GetGrabberConfigurationParameterDefinitions(FGHANDLE grabberHandle);

/**
 * \page APIDefinesAndMacros
 * \section ParameterCallback
 *     Callback function which will be called during execution of KYFG_GetGrabberConfigurationParameterDefinitions()
    or KYFG_GetCameraConfigurationParameterDefinitions()
 * \code{.c}
    typedef void(KYFG_CALLCONV *ParameterCallback)
        (void* userContext, 
        NodeDescriptor* nodeDescriptor, 
        int grouppingLevel);
 * \endcode

 *    
*/
KAYA_API FGSTATUS KY_RegisterCameraConfigurationParameterCallback(CAMHANDLE camHandle, KY_ParameterCallback userFunc, void* userContext);

KAYA_API FGSTATUS KY_UnregisterCameraConfigurationParameterCallback(CAMHANDLE camHandle, KY_ParameterCallback userFunc);

KAYA_API FGSTATUS KY_GetCameraConfigurationParameterDefinitions(CAMHANDLE camHandle);


#ifdef __cplusplus
} // extern "C" {
#endif

#ifndef __cplusplus
#include <stdint.h>
#endif

// UNDER DEVELOPMENT:
// DO NOT USE CODE BELOW IN YOUR PROGRAM!!!
// This enum must be kept is sync with all <MaskedIntReg Name="MonitoringStepXXX"> definitions in KYFGLib.xml
typedef enum _KY_MonitoringSteps
{
    KY_MonitoringStep_InvalidateParameters    = ((uint32_t)1 << 0),
    KY_MonitoringStep_CRCErrorCounter         = ((uint32_t)1 << 1),
    KY_MonitoringStep_CheckCameraConnectivity = ((uint32_t)1 << 2),
    KY_MonitoringStep_PoCXP_ADC_Parameters    = ((uint32_t)1 << 3),
    KY_MonitoringStep_LinkStateAndLedUpdate   = ((uint32_t)1 << 4),
    KY_MonitoringStep_DeviceTemperature       = ((uint32_t)1 << 5),
}KY_MonitoringSteps;


#endif  // #ifndef KYFG_LIB_H_