/*
 @licstart  The following is the entire license notice for the JavaScript code in this file.

 The MIT License (MIT)

 Copyright (C) 1997-2020 by Dimitri van Heesch

 Permission is hereby granted, free of charge, to any person obtaining a copy of this software
 and associated documentation files (the "Software"), to deal in the Software without restriction,
 including without limitation the rights to use, copy, modify, merge, publish, distribute,
 sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:

 The above copyright notice and this permission notice shall be included in all copies or
 substantial portions of the Software.

 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
 BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
 DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

 @licend  The above is the entire license notice for the JavaScript code in this file
*/
var NAVTREE =
[
  [ "API Data Book", "index.html", [
    [ "Revision History", "_revision__history.html", null ],
    [ "Introduction", "_introduction.html", null ],
    [ "Overview", "_overview.html", null ],
    [ "API Notes and Limitations", "_a_p_i__notes_and__limitations.html", null ],
    [ "Defines", "_k_y_v_p_defines.html", [
      [ "KYVP_TAP_GEOMETRY", "_k_y_v_p_defines.html#KYVP_TAP_GEOMETRY", null ],
      [ "BITS_DEPTH_SHIFT_MODE", "_k_y_v_p_defines.html#BITS_DEPTH_SHIFT_MODE", null ],
      [ "SAVED_IMAGE_FORMAT", "_k_y_v_p_defines.html#SAVED_IMAGE_FORMAT", null ],
      [ "KYVP_INFO_DATATYPE", "_k_y_v_p_defines.html#KYVP_INFO_DATATYPE", null ],
      [ "KYVP_TL_INFO_CMD", "_k_y_v_p_defines.html#KYVP_TL_INFO_CMD", null ],
      [ "KYVP_DEVICE_INFO_CMD", "_k_y_v_p_defines.html#KYVP_DEVICE_INFO_CMD", null ],
      [ "KYVP_ACQ_START_FLAGS", "_k_y_v_p_defines.html#KYVP_ACQ_START_FLAGS", null ],
      [ "KYVP_ACQ_STOP_FLAGS", "_k_y_v_p_defines.html#KYVP_ACQ_STOP_FLAGS", null ],
      [ "KYVP_STREAM_INFO_CMD", "_k_y_v_p_defines.html#KYVP_STREAM_INFO_CMD", null ],
      [ "KYVP_BUFFER_INFO_CMD", "_k_y_v_p_defines.html#KYVP_BUFFER_INFO_CMD", null ],
      [ "KYVP_EVENT_TYPE", "_k_y_v_p_defines.html#KYVP_EVENT_TYPE", null ],
      [ "KYVP_EVENT_INFO_CMD", "_k_y_v_p_defines.html#KYVP_EVENT_INFO_CMD", null ],
      [ "KYVP_EVENT_DATA_INFO_CMD", "_k_y_v_p_defines.html#KYVP_EVENT_DATA_INFO_CMD", null ],
      [ "KYVP_LINK_STATUS", "_k_y_v_p_defines.html#KYVP_LINK_STATUS", null ],
      [ "KYVP_LINK_STATUS_CMD", "_k_y_v_p_defines.html#KYVP_LINK_STATUS_CMD", null ],
      [ "DS_PACKED_DATA_MODE", "_k_y_v_p_defines.html#DS_PACKED_DATA_MODE", null ],
      [ "DS_PACKED_DATA_PROCESSING_MODE", "_k_y_v_p_defines.html#DS_PACKED_DATA_PROCESSING_MODE", null ]
    ] ],
    [ "Foundation", "_k_y_foundation__types.html", [
      [ "KY_BOOL", "_k_y_foundation__types.html#KY_BOOL", null ]
    ] ],
    [ "KYVPLibTL Library", "_k_y_v_p_lib_t_l.html", [
      [ "KYVPLibTL Introduction", "_k_y_v_p_lib_t_l__introduction.html", null ],
      [ "KYVPLibTL Handles", "_k_y_v_p_lib_t_l__handles.html", [
        [ "Library handles", "_k_y_v_p_lib_t_l__handles.html#KYVPLibTL_HandlesTable", null ],
        [ "KYVP_TL_HANDLE", "_k_y_v_p_lib_t_l__handles.html#KYVP_TL_HANDLE", null ],
        [ "KYVP_PCI_INTERFACE_HANDLE", "_k_y_v_p_lib_t_l__handles.html#KYVP_PCI_INTERFACE_HANDLE", null ],
        [ "KYVP_DEVICE_HANDLE", "_k_y_v_p_lib_t_l__handles.html#KYVP_DEVICE_HANDLE", null ],
        [ "KYVP_REMOTE_DEVICE_HANDLE", "_k_y_v_p_lib_t_l__handles.html#KYVP_REMOTE_DEVICE_HANDLE", null ],
        [ "KYVP_STREAM_HANDLE", "_k_y_v_p_lib_t_l__handles.html#KYVP_STREAM_HANDLE", null ],
        [ "KYVP_BUFFER_HANDLE", "_k_y_v_p_lib_t_l__handles.html#KYVP_BUFFER_HANDLE", null ]
      ] ],
      [ "KYVPLibTL Structs", "_k_y_v_p_lib_t_l__structs.html", [
        [ "KYVPLIBTL_EVENT_NEW_BUFFER_DATA", "_k_y_v_p_lib_t_l__structs.html#KYVPLIBTL_EVENT_NEW_BUFFER_DATA", null ],
        [ "KYVPLibTL_GetPortUrl_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_GetPortUrl_Args", null ],
        [ "KYVPLibTL_ReadPort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_ReadPort_Args", null ],
        [ "KYVPLibTL_WritePort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_WritePort_Args", null ],
        [ "KYVPLibTL_PCIInterface_GetPortUrl_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_PCIInterface_GetPortUrl_Args", null ],
        [ "KYVPLibTL_PCIInterface_ReadPort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_PCIInterface_ReadPort_Args", null ],
        [ "KYVPLibTL_PCIInterface_WritePort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_PCIInterface_WritePort_Args", null ],
        [ "KYVPLibTL_Device_GetPortUrl_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_Device_GetPortUrl_Args", null ],
        [ "KYVPLibTL_Device_ReadPort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_Device_ReadPort_Args", null ],
        [ "KYVPLibTL_Device_WritePort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_Device_WritePort_Args", null ],
        [ "KYVPLibTL_RemoteDevice_GetPortUrl_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_RemoteDevice_GetPortUrl_Args", null ],
        [ "KYVPLibTL_RemoteDevice_ReadPort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_RemoteDevice_ReadPort_Args", null ],
        [ "KYVPLibTL_RemoteDevice_WritePort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_RemoteDevice_WritePort_Args", null ],
        [ "KYVPLibTL_Stream_GetPortUrl_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_Stream_GetPortUrl_Args", null ],
        [ "KYVPLibTL_Stream_ReadPort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_Stream_ReadPort_Args", null ],
        [ "KYVPLibTL_Stream_WritePort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_Stream_WritePort_Args", null ],
        [ "KYVPLibTL_TLOpen_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_TLOpen_Args", null ],
        [ "KYVPLibTL_TLClose_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_TLClose_Args", null ],
        [ "KYVPLibTL_TLGetInfo_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_TLGetInfo_Args", null ],
        [ "KYVPLibTL_TLGetNumInterfaces_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_TLGetNumInterfaces_Args", null ],
        [ "KYVPLibTL_TLGetInterfaceID_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_TLGetInterfaceID_Args", null ],
        [ "KYVPLibTL_TLGetPCIInterfaceInfo_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_TLGetPCIInterfaceInfo_Args", null ],
        [ "KYVPLibTL_TLOpenInterface_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_TLOpenInterface_Args", null ],
        [ "KYVPLibTL_TLUpdatePCIInterfaceList_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_TLUpdatePCIInterfaceList_Args", null ],
        [ "KYVPLibTL_IFClose_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_IFClose_Args", null ],
        [ "KYVPLibTL_IFGetInfo_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_IFGetInfo_Args", null ],
        [ "KYVPLibTL_IFGetNumDevices_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_IFGetNumDevices_Args", null ],
        [ "KYVPLibTL_IFGetDeviceID_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_IFGetDeviceID_Args", null ],
        [ "KYVPLibTL_IFUpdateDeviceList_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_IFUpdateDeviceList_Args", null ],
        [ "KYVPLibTL_IFGetDeviceInfo_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_IFGetDeviceInfo_Args", null ],
        [ "KYVPLibTL_IFOpenDevice_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_IFOpenDevice_Args", null ],
        [ "KYVPLibTL_IFRegisterEvent_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_IFRegisterEvent_Args", null ],
        [ "KYVPLibTL_IFUnregisterEvent_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_IFUnregisterEvent_Args", null ],
        [ "KYVPLibTL_DevGetPort_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DevGetPort_Args", null ],
        [ "KYVPLibTL_DevGetNumDataStreams_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DevGetNumDataStreams_Args", null ],
        [ "KYVPLibTL_DevGetDataStreamID_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DevGetDataStreamID_Args", null ],
        [ "KYVPLibTL_DevOpenDataStream_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DevOpenDataStream_Args", null ],
        [ "KYVPLibTL_DevGetInfo_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DevGetInfo_Args", null ],
        [ "KYVPLibTL_DevClose_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DevClose_Args", null ],
        [ "KYVPLibTL_DSAnnounceBuffer_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSAnnounceBuffer_Args", null ],
        [ "KYVPLibTL_DSAllocAndAnnounceBuffer_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSAllocAndAnnounceBuffer_Args", null ],
        [ "KYVPLibTL_DSStartAcquisition_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSStartAcquisition_Args", null ],
        [ "KYVPLibTL_DSStopAcquisition_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSStopAcquisition_Args", null ],
        [ "KYVPLibTL_DSGetInfo_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSGetInfo_Args", null ],
        [ "KYVPLibTL_DSGetBufferID_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSGetBufferID_Args", null ],
        [ "KYVPLibTL_DSClose_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSClose_Args", null ],
        [ "KYVPLibTL_DSRevokeBuffer_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSRevokeBuffer_Args", null ],
        [ "KYVPLibTL_DSQueueBuffer_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSQueueBuffer_Args", null ],
        [ "KYVPLibTL_DSGetBufferInfo_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSGetBufferInfo_Args", null ],
        [ "KYVPLibTL_DSRegisterEvent_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSRegisterEvent_Args", null ],
        [ "KYVPLibTL_DSUnregisterEvent_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_DSUnregisterEvent_Args", null ],
        [ "KYVPLibTL_EventGetData_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_EventGetData_Args", null ],
        [ "KYVPLibTL_EventGetDataInfo_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_EventGetDataInfo_Args", null ],
        [ "KYVPLibTL_EventGetInfo_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_EventGetInfo_Args", null ],
        [ "KYVPLibTL_EventFlush_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_EventFlush_Args", null ],
        [ "KYVPLibTL_EventKill_Args", "_k_y_v_p_lib_t_l__structs.html#KYVPLibTL_EventKill_Args", null ]
      ] ],
      [ "KYVPLibTL Functions", "_k_y_v_p_lib_t_l__functions.html", [
        [ "KYVPLibTL System Functions", "_k_y_v_p_lib_t_l__functions__system.html", [
          [ "KYVPLibTL_TLOpen()", "_k_y_v_p_lib_t_l__functions__system.html#KYVPLibTL_TLOpen", null ],
          [ "KYVPLibTL_TLClose()", "_k_y_v_p_lib_t_l__functions__system.html#KYVPLibTL_TLClose", null ],
          [ "KYVPLibTL_TLGetInfo()", "_k_y_v_p_lib_t_l__functions__system.html#KYVPLibTL_TLGetInfo", null ],
          [ "KYVPLibTL_TLGetNumInterfaces()", "_k_y_v_p_lib_t_l__functions__system.html#KYVPLibTL_TLGetNumInterfaces", null ],
          [ "KYVPLibTL_TLGetInterfaceID()", "_k_y_v_p_lib_t_l__functions__system.html#KYVPLibTL_TLGetInterfaceID", null ],
          [ "KYVPLibTL_TLGetInterfaceInfo()", "_k_y_v_p_lib_t_l__functions__system.html#KYVPLibTL_TLGetInterfaceInfo", null ],
          [ "KYVPLibTL_TLOpenInterface()", "_k_y_v_p_lib_t_l__functions__system.html#KYVPLibTL_TLOpenInterface", null ],
          [ "KYVPLibTL_TLUpdateInterfaceList()", "_k_y_v_p_lib_t_l__functions__system.html#KYVPLibTL_TLUpdateInterfaceList", null ]
        ] ],
        [ "KYVPLibTL Interface Functions", "_k_y_v_p_lib_t_l__functions__interface.html", [
          [ "KYVPLibTL_IFClose()", "_k_y_v_p_lib_t_l__functions__interface.html#KYVPLibTL_IFClose", null ],
          [ "KYVPLibTL_IFGetInfo()", "_k_y_v_p_lib_t_l__functions__interface.html#KYVPLibTL_IFGetInfo", null ],
          [ "KYVPLibTL_IFGetNumDevices()", "_k_y_v_p_lib_t_l__functions__interface.html#KYVPLibTL_IFGetNumDevices", null ],
          [ "KYVPLibTL_IFGetDeviceID()", "_k_y_v_p_lib_t_l__functions__interface.html#KYVPLibTL_IFGetDeviceID", null ],
          [ "KYVPLibTL_IFUpdateDeviceList()", "_k_y_v_p_lib_t_l__functions__interface.html#KYVPLibTL_IFUpdateDeviceList", null ],
          [ "KYVPLibTL_IFGetDeviceInfo()", "_k_y_v_p_lib_t_l__functions__interface.html#KYVPLibTL_IFGetDeviceInfo", null ],
          [ "KYVPLibTL_IFOpenDevice()", "_k_y_v_p_lib_t_l__functions__interface.html#KYVPLibTL_IFOpenDevice", null ],
          [ "KYVPLibTL_IFRegisterEvent()", "_k_y_v_p_lib_t_l__functions__interface.html#KYVPLibTL_IFRegisterEvent", null ],
          [ "KYVPLibTL_IFUnregisterEvent()", "_k_y_v_p_lib_t_l__functions__interface.html#KYVPLibTL_IFUnregisterEvent", null ]
        ] ],
        [ "KYVPLibTL Stream Functions", "_k_y_v_p_lib_t_l__functions__stream.html", [
          [ "KYVPLibTL_DSAnnounceBuffer()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSAnnounceBuffer", null ],
          [ "KYVPLibTL_DSAllocAndAnnounceBuffer()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSAllocAndAnnounceBuffer", null ],
          [ "KYVPLibTL_DSStartAcquisition()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSStartAcquisition", null ],
          [ "KYVPLibTL_DSStopAcquisition()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSStopAcquisition", null ],
          [ "KYVPLibTL_DSGetInfo()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSGetInfo", null ],
          [ "KYVPLibTL_DSGetBufferID()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSGetBufferID", null ],
          [ "KYVPLibTL_DSClose()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSClose", null ],
          [ "KYVPLibTL_DSRevokeBuffer()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSRevokeBuffer", null ],
          [ "KYVPLibTL_DSQueueBuffer()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSQueueBuffer", null ],
          [ "KYVPLibTL_DSGetBufferInfo()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSGetBufferInfo", null ],
          [ "KYVPLibTL_DSRegisterEvent()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSRegisterEvent", null ],
          [ "KYVPLibTL_DSUnregisterEvent()", "_k_y_v_p_lib_t_l__functions__stream.html#KYVPLibTL_DSUnregisterEvent", null ]
        ] ],
        [ "KYVPLibTL Event Functions", "_k_y_v_p_lib_t_l__functions__event.html", [
          [ "KYVPLibTL_EventGetData()", "_k_y_v_p_lib_t_l__functions__event.html#KYVPLibTL_EventGetData", null ],
          [ "KYVPLibTL_EventGetDataInfo()", "_k_y_v_p_lib_t_l__functions__event.html#KYVPLibTL_EventGetDataInfo", null ],
          [ "KYVPLibTL_EventGetInfo()", "_k_y_v_p_lib_t_l__functions__event.html#KYVPLibTL_EventGetInfo", null ],
          [ "KYVPLibTL_EventFlush()", "_k_y_v_p_lib_t_l__functions__event.html#KYVPLibTL_EventFlush", null ],
          [ "KYVPLibTL_EventKill()", "_k_y_v_p_lib_t_l__functions__event.html#KYVPLibTL_EventKill", null ]
        ] ]
      ] ]
    ] ],
    [ "Parameters Handler Library", "_parameters_handler_library.html", [
      [ "KYVP_ParametersHandler Description", "_k_y_v_p__parameters_handler_library_description.html", null ],
      [ "Parameters Handler Library Structs", "_k_y_v_p_parameters_handler_structs.html", [
        [ "KYVP_COLLECTION_HANDLE", "_k_y_v_p_parameters_handler_structs.html#KYVP_COLLECTION_HANDLE", null ],
        [ "CALLBACK FUNCTIONS", "_k_y_v_p_parameters_handler_structs.html#CALLBACK_FUNCTIONS", null ],
        [ "ENUM KYParametersHandler_TransportOperationType", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_TransportOperationType", null ],
        [ "KYParametersHandler_TransportCallParams_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_TransportCallParams_Args", null ],
        [ "KYParametersHandler_InitParameters", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_InitParameters", null ],
        [ "KYParametersHandler_Initialize_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_Initialize_Args", null ],
        [ "KYParametersHandler_CreateParameterCollection_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_CreateParameterCollection_Args", null ],
        [ "KYParametersHandler_DeleteParameterCollection_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_DeleteParameterCollection_Args", null ],
        [ "KYParametersHandler_RegisterParameterCollectionTransport_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_RegisterParameterCollectionTransport_Args", null ],
        [ "KYParametersHandler_UnregisterParameterCollectionTransport_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_UnregisterParameterCollectionTransport_Args", null ],
        [ "KYParametersHandler_RegisterParameterCollectionCallback_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_RegisterParameterCollectionCallback_Args", null ],
        [ "KYParametersHandler_UnregisterParameterCollectionCallback_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_UnregisterParameterCollectionCallback_Args", null ],
        [ "KYParametersHandler_InitializeParameterCollection_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_InitializeParameterCollection_Args", null ],
        [ "KYParametersHandler_GetNodeDescriptor_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_GetNodeDescriptor_Args", null ],
        [ "KYParametersHandler_GetNodeDescriptorsArray_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_GetNodeDescriptorsArray_Args", null ],
        [ "KYParametersHandler_GetValue_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_GetValue_Args", null ],
        [ "KYParametersHandler_SetValue_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_SetValue_Args", null ],
        [ "KYParametersHandler_GetValueEnumAsString_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_GetValueEnumAsString_Args", null ],
        [ "KYParametersHandler_SetValueEnumByValueName_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_SetValueEnumByValueName_Args", null ],
        [ "KYParametersHandler_InvalidateParameterCollection_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_InvalidateParameterCollection_Args", null ],
        [ "KYParametersHandler_InvalidateParameter_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_InvalidateParameter_Args", null ],
        [ "KYParametersHandler_SaveToFile_Args", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_SaveToFile_Args", null ],
        [ "KYParametersHandler_GetParameterAttributeValueArgs", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_GetParameterAttributeValueArgs", null ],
        [ "KYParametersHandler_IsParameterAvailableArgs", "_k_y_v_p_parameters_handler_structs.html#KYParametersHandler_IsParameterAvailableArgs", null ]
      ] ],
      [ "KYVP Node Descriptor", "_k_y_v_p__node_descriptor.html", [
        [ "KYVP_NodeDescriptorType", "_k_y_v_p__node_descriptor.html#KYVP_NodeDescriptorType", null ],
        [ "KYVP_ParameterInterfaceType", "_k_y_v_p__node_descriptor.html#KYVP_ParameterInterfaceType", null ],
        [ "KYVP_ParameterRepresentation", "_k_y_v_p__node_descriptor.html#KYVP_ParameterRepresentation", null ],
        [ "KYVP_ParameterVisibility", "_k_y_v_p__node_descriptor.html#KYVP_ParameterVisibility", null ],
        [ "KYVP_NodeDescriptor", "_k_y_v_p__node_descriptor.html#KYVP_NodeDescriptor", null ],
        [ "KYVP_ParameterAttributeType", "_k_y_v_p__node_descriptor.html#KYVP_ParameterAttributeType", null ]
      ] ],
      [ "Parameters Handler Functions", "_k_y_parameters_handler__functions.html", null ]
    ] ],
    [ "Image Processing Library", "_k_y_img_proc.html", [
      [ "Introduction", "_k_y_img_proc__introduction.html", null ],
      [ "Image Processing Library Functions", "_k_y_img_proc__functions.html", null ],
      [ "Image Processing Library Structs", "_k_y_v_p_image_processing_structs.html", [
        [ "KYVP_VIDEO_PIXELIF", "_k_y_v_p_image_processing_structs.html#KYVP_VIDEO_PIXELIF", null ],
        [ "KYVP_PIXEL_FORMAT", "_k_y_v_p_image_processing_structs.html#KYVP_PIXEL_FORMAT", null ],
        [ "KYIMGPROC_PIXEL_DECODER", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_PIXEL_DECODER", null ],
        [ "KYIMGPROC_VIDEO_RECORDER_HANDLE", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_VIDEO_RECORDER_HANDLE", null ],
        [ "KYIMGPROC_IMAGE_CONVERTER_HANDLE", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_IMAGE_CONVERTER_HANDLE", null ],
        [ "VPImage", "_k_y_v_p_image_processing_structs.html#VPImage", null ],
        [ "PackedDataInfo", "_k_y_v_p_image_processing_structs.html#PackedDataInfo", null ],
        [ "KYIMGPROC_InitVideoRecorderArgs", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_InitVideoRecorderArgs", null ],
        [ "KYIMGPROC_DeleteVideoRecorderArgs", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_DeleteVideoRecorderArgs", null ],
        [ "KYIMGPROC_InitImageConverterArgs", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_InitImageConverterArgs", null ],
        [ "KYIMGPROC_DeleteImageConverterArgs", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_DeleteImageConverterArgs", null ],
        [ "KYIMGPROC_ConvertParam_Args", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_ConvertParam_Args", null ],
        [ "KYIMGPROC_SaveImageArgs", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_SaveImageArgs", null ],
        [ "KYIMGPROC_SaveRawArgs", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_SaveRawArgs", null ],
        [ "KYIMGPROC_SaveVideoArgs", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_SaveVideoArgs", null ],
        [ "KYIMGPROC_WriteVideoFrameArgs", "_k_y_v_p_image_processing_structs.html#KYIMGPROC_WriteVideoFrameArgs", null ],
        [ "KYImgProc_StopVideoRecordArgs", "_k_y_v_p_image_processing_structs.html#KYImgProc_StopVideoRecordArgs", null ],
        [ "KYImgProc_GetPackedDataInfo_Args", "_k_y_v_p_image_processing_structs.html#KYImgProc_GetPackedDataInfo_Args", null ],
        [ "KYImgProc_PreproccessPackedData_Args", "_k_y_v_p_image_processing_structs.html#KYImgProc_PreproccessPackedData_Args", null ],
        [ "KYImgProc_GetPixelFormat_Args", "_k_y_v_p_image_processing_structs.html#KYImgProc_GetPixelFormat_Args", null ],
        [ "KYImgProc_GetPixelFormatByName_Args", "_k_y_v_p_image_processing_structs.html#KYImgProc_GetPixelFormatByName_Args", null ]
      ] ]
    ] ],
    [ "Library Exiting", "_library__exiting.html", null ],
    [ "API Examples", "_a_p_i__examples.html", null ],
    [ "Collecting Diagnostic information", "_collecting__diagnostic_information.html", null ],
    [ "KYVPExtension", "_k_y_v_p_extension.html", [
      [ "KYVP_IO_AUX_DATA", "_k_y_v_p_extension.html#KYVP_IO_AUX_DATA", null ],
      [ "KYVP_FRAME_AUX_DATA", "_k_y_v_p_extension.html#KYVP_FRAME_AUX_DATA", null ],
      [ "KYVP_AUX_DATA", "_k_y_v_p_extension.html#KYVP_AUX_DATA", null ],
      [ "KYVP_UPDATE_CALLBACK", "_k_y_v_p_extension.html#KYVP_UPDATE_CALLBACK", null ],
      [ "KYVP_DATA_STREAM_CALLBACK", "_k_y_v_p_extension.html#KYVP_DATA_STREAM_CALLBACK", null ],
      [ "KYVP_AUX_DATA_CALLBACK", "_k_y_v_p_extension.html#KYVP_AUX_DATA_CALLBACK", null ],
      [ "KYVP_SERIAL_CALLBACK", "_k_y_v_p_extension.html#KYVP_SERIAL_CALLBACK", null ],
      [ "KYVP_PCI_INTERFACE_EVENT_CALLBACK", "_k_y_v_p_extension.html#KYVP_PCI_INTERFACE_EVENT_CALLBACK", null ]
    ] ],
    [ "KYVPExtension Structs", "_k_y_v_p_extension__structs.html", [
      [ "KYVPExtension_RemoteDevice_DirectHWReadReg_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_RemoteDevice_DirectHWReadReg_Args", null ],
      [ "KYVPExtension_RemoteDevice_DirectHWWriteReg_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_RemoteDevice_DirectHWWriteReg_Args", null ],
      [ "KYVPExtension_RemoteDevice_SetImageDetails_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_RemoteDevice_SetImageDetails_Args", null ],
      [ "KYVPExtension_PCIInterface_DirectHWReadReg_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_PCIInterface_DirectHWReadReg_Args", null ],
      [ "KYVPExtension_PCIInterface_DirectHWWriteReg_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_PCIInterface_DirectHWWriteReg_Args", null ],
      [ "KYVPExtension_PCIInterface_LockLink_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_PCIInterface_LockLink_Args", null ],
      [ "KYVPExtension_PCIInterface_UnlockLink_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_PCIInterface_UnlockLink_Args", null ],
      [ "KYVPExtension_PCIInterface_ReadLink_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_PCIInterface_ReadLink_Args", null ],
      [ "KYVPExtension_PCIInterface_WriteLink_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_PCIInterface_WriteLink_Args", null ],
      [ "KYVPExtension_PCIInterface_LinkGetStatus_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_PCIInterface_LinkGetStatus_Args", null ],
      [ "KYVPExtension_RemoteDevice_GetParamsCollectionHandle_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_RemoteDevice_GetParamsCollectionHandle_Args", null ],
      [ "DeviceLinkDefinition", "_k_y_v_p_extension__structs.html#DeviceLinkDefinition", null ],
      [ "DeviceDefinition", "_k_y_v_p_extension__structs.html#DeviceDefinition", null ],
      [ "KYVPExtension_DetectDevicesManually_Args", "_k_y_v_p_extension__structs.html#KYVPExtension_DetectDevicesManually_Args", null ]
    ] ],
    [ "Topics", "topics.html", "topics" ],
    [ "Data Structures", "annotated.html", [
      [ "Data Structures", "annotated.html", "annotated_dup" ],
      [ "Data Fields", "functions.html", [
        [ "All", "functions.html", "functions_dup" ],
        [ "Functions", "functions_func.html", "functions_func" ],
        [ "Variables", "functions_vars.html", null ]
      ] ]
    ] ]
  ] ]
];

var NAVTREEINDEX =
[
"_a_p_i__examples.html",
"group___interface_module.html#ga5f9d185586f02c7b6e8d9bf61d0a5f3f",
"struct___k_y_parameters_handler___initialize_parameter_collection___args.html#afa1c318febcdb30d29e249bdacdc1cca",
"struct___k_y_v_p_extension___data_stream___callback___unregister___args.html#a599858d1cd2b475f9b97b9a35d7ae50c"
];

var SYNCONMSG = 'click to disable panel synchronisation';
var SYNCOFFMSG = 'click to enable panel synchronisation';