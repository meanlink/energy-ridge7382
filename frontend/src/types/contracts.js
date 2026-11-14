/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,period:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,report_id:number,period:number,status:string,ready:boolean,integrity:string,mode:string,checksum:string,filename:string,repair_count:number}} BriefingDelivery */
export {};
