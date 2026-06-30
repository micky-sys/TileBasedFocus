#ifndef KYFG_LIBA_H_
#define KYFG_LIBA_H_

#include "KYFGLib.h"

#include "KYVPLibTL.h"
#include "KYVPParametersHandler.h"
#include "KYVPImageProcessing.h"
#include "KYVPLibExtension.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * \section KYFG_Grabber_Get_KYVP_PCI_INTERFACE_HANDLE()
 * @brief   Returns underlying KYVP_PCI_INTERFACE_HANDLE for an open FGHANDLE
 * @param   [in] handle - API handle to Frame Grabber device as returned by KYFG_Open() or KYFG_OpenEx()
 * \param   [out] _pKYVP_PCI_INTERFACE_HANDLE - pointer to KYVP_PCI_INTERFACE_HANDLE variable that will be set by this function
 * @return  FGSTATUS - Status and error report.
 */
KAYA_API FGSTATUS KYFG_Grabber_Get_KYVP_PCI_INTERFACE_HANDLE(FGHANDLE handle, KYVP_PCI_INTERFACE_HANDLE* _pKYVP_PCI_INTERFACE_HANDLE);

/**
 * \section KYFG_Grabber_Get_KYVP_COLLECTION_HANDLE()
 * @brief   Returns underlying KYVP_COLLECTION_HANDLE for an open FGHANDLE
 * @param   [in] handle - API handle to Frame Grabber device as returned by KYFG_Open() or KYFG_OpenEx()
 * \param   [out] _pKYVP_COLLECTION_HANDLE - pointer to KYVP_COLLECTION_HANDLE variable that will be set by this function
 * @return  FGSTATUS - Status and error report.
 */
KAYA_API FGSTATUS KYFG_Grabber_Get_KYVP_COLLECTION_HANDLE(FGHANDLE handle, KYVP_COLLECTION_HANDLE* _pKYVP_COLLECTION_HANDLE);

/**
 * \section KYFG_Camera_Get_KYVP_DEVICE_HANDLE()
 * @brief   Returns underlying KYVP_DEVICE_HANDLE for an open CAMHANDLE
 * @param   [in] camHandle - CAMHANDLE used to open a camera with KYFG_CameraOpen() or KYFG_CameraOpen2()
 * \param   [out] _pKYVP_DEVICE_HANDLE - pointer to KYVP_DEVICE_HANDLE variable that will be set by this function
 * @return  FGSTATUS - Status and error report.
 */
KAYA_API FGSTATUS KYFG_Camera_Get_KYVP_DEVICE_HANDLE(CAMHANDLE camHandle, KYVP_DEVICE_HANDLE* _pKYVP_DEVICE_HANDLE);

#ifdef __cplusplus
} // extern "C" 
#endif

#ifdef __cplusplus
#include "KYFGLibAcpp.h"
#endif

#endif  // #ifndef KYFG_LIBA_H_

