(() => {
 "use strict";
 const core = window.dashboardCore;
 const fallback = document.getElementById("storage-backup-output") || document.getElementById("storage-health-output");
 if (!core || core.version !== 1) {
  if (fallback) fallback.textContent = "ماژول ذخیره‌سازی بارگذاری نشد: قرارداد dashboardCore v1 در دسترس نیست.";
  return;
 }
 const {api,el,appendText}=core;
 const required = ["storage-health-output","storage-backups-list","storage-backup-output","refresh-storage-health","refresh-storage-backups","create-storage-backup","prune-storage-backups","prune-restore-staging"];
 const missing = required.filter(id => !el(id));
 if (missing.length) {
  if (fallback) fallback.textContent = "ماژول ذخیره‌سازی به کنترل‌های لازم دسترسی ندارد: " + missing.join(", ");
  return;
 }
 let healthSequence = 0;
 let backupsSequence = 0;
 let mutationActive = false;

 async function refreshStorageHealth() {
  const sequence = ++healthSequence;
  const output=el("storage-health-output");
  output.textContent="در حال بررسی فقط‌خواندنی سلامت ذخیره‌سازی…";
  try {
   const result=await api("/api/health/storage");
   if(sequence!==healthSequence)return;
   output.textContent=JSON.stringify(result,null,2);
  } catch(error) {
   if(sequence===healthSequence) output.textContent="بررسی سلامت ذخیره‌سازی ناموفق بود: "+error.message;
  }
 }
 async function refreshStorageBackups() {
  const sequence=++backupsSequence;
  const output=el("storage-backups-list");
  output.replaceChildren();
  try {
   const result=await api("/api/storage/backups");
   if(sequence!==backupsSequence)return;
   if(!result.items || !result.items.length) {
    output.textContent="پشتیبان معتبری پیدا نشد.";
    return;
   }
   result.items.forEach(item=>{
    const card=document.createElement("article");
    card.className="cloud-candidate";
    appendText(card,"p",item.id);
    appendText(card,"p","تاریخ: "+(item.created_at||"نامشخص")+"؛ قالب: "+item.format_version+"؛ دیتابیس‌ها: "+(item.database_roles||[]).join("، "));
    appendText(card,"p",item.complete?"پشتیبان کامل سه‌دیتابیسی":"پشتیبان قدیمی/ناقص؛ فقط دیتابیس‌های موجود بازیابی می‌شوند","muted");
    const restore=document.createElement("button");
    restore.type="button";
    restore.textContent="بازیابی آزمایشی در پوشهٔ جدا";
    restore.addEventListener("click",()=>stageBackupRestore(item.id,restore));
    card.append(restore);
    output.append(card);
   });
  } catch(error) {
   if(sequence===backupsSequence) output.textContent="فهرست پشتیبان‌ها دریافت نشد: "+error.message;
  }
 }
 async function withMutation(button, action) {
  if(mutationActive || !button || button.disabled)return;
  mutationActive=true;
  button.disabled=true;
  try { await action(); }
  finally { button.disabled=false; mutationActive=false; }
 }
 async function stageBackupRestore(backupId,button) {
  if(mutationActive || !window.confirm("از این پشتیبان در یک پوشهٔ staging جداگانه بازیابی و اعتبارسنجی شود؟ دیتابیس‌های زنده تغییر نمی‌کنند و هیچ جایگزینی خودکاری انجام نمی‌شود.")) return;
  await withMutation(button,async()=>{
   const output=el("storage-backup-output");
   output.textContent="در حال اعتبارسنجی و بازیابی پشتیبان در محیط staging…";
   try {
    const result=await api("/api/storage/backups/"+encodeURIComponent(backupId)+"/restore",{method:"POST",body:JSON.stringify({confirm:true})});
    output.textContent=JSON.stringify(result,null,2);
   } catch(error) {
    output.textContent="بازیابی آزمایشی ناموفق بود: "+error.message;
   }
  });
 }
 async function pruneStorageBackups() {
  const button=el("prune-storage-backups");
  await withMutation(button,async()=>{
   const output=el("storage-backup-output");
   output.textContent="در حال بررسی سیاست نگهداری پشتیبان‌ها…";
   try {
    const preview=await api("/api/storage/backups/prune",{method:"POST",body:JSON.stringify({confirm:false,keep_last:3,min_age_days:30})});
    if(!preview.would_delete_count) {
     output.textContent="پشتیبان واجد شرایط حذف وجود ندارد. حداقل سه نسخهٔ آخر و پشتیبان‌های کمتر از ۳۰ روز حفظ می‌شوند.";
     return;
    }
    const ids=(preview.candidates||[]).map(item=>item.id).join("\n");
    if(!window.confirm("این پشتیبان‌ها بیش از ۳۰ روز عمر دارند و جزو سه نسخهٔ آخر نیستند. حذف دائمی انجام شود؟\n\n"+ids+"\n\nاین کار قابل بازگشت نیست.")) {
     output.textContent="پاک‌سازی لغو شد؛ هیچ پشتیبانی حذف نشد.";
     return;
    }
    const result=await api("/api/storage/backups/prune",{method:"POST",body:JSON.stringify({confirm:true,keep_last:3,min_age_days:30})});
    output.textContent=JSON.stringify(result,null,2);
    await refreshStorageBackups();
   } catch(error) {
    output.textContent="پاک‌سازی پشتیبان‌ها ناموفق بود: "+error.message;
   }
  });
 }
 async function pruneRestoreStaging() {
  const button=el("prune-restore-staging");
  await withMutation(button,async()=>{
   const output=el("storage-backup-output");
   output.textContent="در حال بررسی بازیابی‌های آزمایشی قدیمی و اعتبارسنجی دیتابیس‌ها…";
   try {
    const policy={confirm:false,keep_last:1,min_age_days:7};
    const preview=await api("/api/storage/restore-staging/prune",{method:"POST",body:JSON.stringify(policy)});
    if(!preview.would_delete_count) {
     output.textContent="بازیابی آزمایشی واجد شرایط حذف وجود ندارد. حداقل یک نسخهٔ اخیر و نسخه‌های کمتر از ۷ روز حفظ می‌شوند.";
     return;
    }
    const ids=(preview.candidates||[]).map(item=>item.id).join("\n");
    if(!window.confirm("فقط بازیابی‌های آزمایشی معتبر که بیش از ۷ روز عمر دارند و جزو آخرین نسخه نیستند حذف می‌شوند. پشتیبان‌های اصلی و دیتابیس‌های زنده تغییر نمی‌کنند. ادامه؟\n\n"+ids+"\n\nاین حذف قابل بازگشت نیست.")) {
     output.textContent="پاک‌سازی بازیابی‌های آزمایشی لغو شد؛ چیزی حذف نشد.";
     return;
    }
    const result=await api("/api/storage/restore-staging/prune",{method:"POST",body:JSON.stringify({confirm:true,keep_last:1,min_age_days:7})});
    output.textContent=JSON.stringify(result,null,2);
   } catch(error) {
    output.textContent="پاک‌سازی بازیابی‌های آزمایشی ناموفق بود: "+error.message;
   }
  });
 }
 async function createStorageBackup() {
  if(mutationActive || !window.confirm("یک پشتیبان کامل از دیتابیس‌های تنظیمات، یادگیری و منابع ابری ساخته شود؟ این کار فایل جدیدی روی دیسک ایجاد می‌کند.")) return;
  await withMutation(el("create-storage-backup"),async()=>{
   const output=el("storage-backup-output");
   output.textContent="در حال ساخت و بررسی پشتیبان کامل…";
   try {
    const result=await api("/api/storage/backups",{method:"POST",body:JSON.stringify({confirm:true})});
    output.textContent=JSON.stringify(result,null,2);
    await refreshStorageBackups();
   } catch(error) {
    output.textContent="ساخت پشتیبان ناموفق بود: "+error.message;
   }
  });
 }
 el("refresh-storage-health").addEventListener("click",refreshStorageHealth);
 el("refresh-storage-backups").addEventListener("click",refreshStorageBackups);
 el("create-storage-backup").addEventListener("click",createStorageBackup);
 el("prune-storage-backups").addEventListener("click",pruneStorageBackups);
 el("prune-restore-staging").addEventListener("click",pruneRestoreStaging);
 refreshStorageHealth();
 refreshStorageBackups();
})();