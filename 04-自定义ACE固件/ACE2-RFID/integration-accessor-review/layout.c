#include "experimental_binding.h"
const uint32_t review_layout[] __attribute__((section(".reviewlayout")))={
 sizeof(struct ex_storage), EX_STORAGE_PAYLOAD,
 offsetof(struct ex_storage,rf), offsetof(struct ex_storage,ex),
 offsetof(struct ex_storage,preload), sizeof(struct rf_state),sizeof(struct ex_state),
 sizeof(struct ph_storage),EX_STORAGE_MAGIC,EX_STORAGE_READY,
 offsetof(struct ph_storage,reader),offsetof(struct ph_reader,lane)
};
