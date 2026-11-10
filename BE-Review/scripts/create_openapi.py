"""Rebuild the DRAFT API interface from a fixed schema, not business implementation."""
from pathlib import Path
import yaml

base = {
 'openapi':'3.1.0',
 'info':{'title':'Waimea WaveCast Public API — PROPOSED CONTRACT ONLY','version':'1.0.0-draft','description':'Endpoints are not yet implemented in scaffold. No real NOAA/ML results included.'},
 'servers':[{'url':'https://apiwaimea.liems.io.vn/api/v1'}],
 'tags':[{'name':x} for x in ['Stations','Observations','Analytics','Machine Learning','Monitoring']],
 'paths':{},
 'components':{'schemas':{
  'Error':{'type':'object','required':['error'],'properties':{'error':{'type':'object','properties':{'code':{'type':'string'},'message':{'type':'string'},'request_id':{'type':'string'},'details':{'type':'object','additionalProperties':True}}}}},
  'Station':{'type':'object','required':['station_id','station_name','local_timezone'],'properties':{'station_id':{'type':'string','example':'51201'},'station_name':{'type':'string'},'local_timezone':{'type':'string','example':'Pacific/Honolulu'},'latitude':{'type':['number','null']},'longitude':{'type':['number','null']}}},
  'Observation':{'type':'object','required':['station_id','observed_at','quality_status'],'properties':{'station_id':{'type':'string'},'observed_at':{'type':'string','format':'date-time'},'wave_height_m':{'type':['number','null']},'dominant_period_s':{'type':['number','null']},'average_period_s':{'type':['number','null']},'mean_wave_direction_deg':{'type':['number','null']},'sea_surface_temp_c':{'type':['number','null']},'quality_status':{'type':'string'},'source_kind':{'type':'string'}}},
  'Prediction':{'type':'object','required':['station_id','target_metric','target_date_hst','feature_cutoff_date_hst','predicted_value_m','issued_at','model'],'properties':{'station_id':{'type':'string'},'target_metric':{'type':'string','enum':['WVHT']},'target_date_hst':{'type':'string','format':'date'},'feature_cutoff_date_hst':{'type':'string','format':'date'},'predicted_value_m':{'type':'number'},'issued_at':{'type':'string','format':'date-time'},'actual_value_m':{'type':['number','null']},'model':{'type':'object','properties':{'model_id':{'type':'integer'},'version':{'type':'string'}}},'quality_status':{'type':'string'}}},
  'ListResponse':{'type':'object','required':['items','has_more'],'properties':{'items':{'type':'array','items':{'type':'object','additionalProperties':True}},'next_cursor':{'type':['string','null']},'has_more':{'type':'boolean'}}},
  'Job':{'type':'object','required':['job_id','status'],'properties':{'job_id':{'type':'integer'},'status':{'type':'string','enum':['running','success','failed','partial','skipped']},'started_at':{'type':['string','null'],'format':'date-time'},'finished_at':{'type':['string','null'],'format':'date-time'},'error_message':{'type':['string','null']}}},
 }},
}

def qp(name,kind='string',required=False,description=None,default=None,enum=None):
 p={'in':'query','name':name,'required':required,'schema':{'type':kind}}
 if description: p['description']=description
 if default is not None: p['schema']['default']=default
 if enum: p['schema']['enum']=enum
 return p

def ep(path,tag,summary,ref='ListResponse',params=None,ok=200):
 base['paths'][path]={'get':{'tags':[tag],'summary':summary,'operationId':'get_'+path.strip('/').replace('/','_').replace('-','_').replace('{','').replace('}',''),
 'parameters':params or [],'responses':{str(ok):{'description':'Proposed response schema','content':{'application/json':{'schema':{'$ref':'#/components/schemas/'+ref}}}},'422':{'description':'Validation error','content':{'application/json':{'schema':{'$ref':'#/components/schemas/Error'}}}},'503':{'description':'Service unavailable'}}}}


ep('/stations/{station_id}','Stations','Station metadata','Station',[{'name':'station_id','in':'path','required':True,'schema':{'type':'string'}}])
ep('/observations','Observations','Paginated NOAA observations','ListResponse',[qp('station_id',default='51201'),qp('start',description='UTC RFC3339 inclusive'),qp('end',description='UTC RFC3339 exclusive'),qp('limit','integer',default=100),qp('cursor')])
ep('/observations/latest','Observations','Latest available NOAA observation','Observation',[qp('station_id',default='51201')])
ep('/metrics/summary','Analytics','Time series day/week/month','ListResponse',[qp('metric',required=True,enum=['WVHT','DPD','APD','MWD','WTMP']),qp('group_by',default='day',enum=['day','week','month']),qp('start_hst'),qp('end_hst')])
ep('/analytics/trend-seasonal','Analytics','Trend seasonal residual','ListResponse',[qp('metric',required=True),qp('period',enum=['week','month']),qp('start_hst'),qp('end_hst')])
ep('/analytics/correlations','Analytics','Paired valid sample correlations','ListResponse',[qp('method',enum=['pearson','spearman']),qp('start_hst'),qp('end_hst')])
ep('/analytics/histogram','Analytics','Histogram data for metric','ListResponse',[qp('metric',required=True),qp('bins','integer',default=20),qp('start_hst'),qp('end_hst')])
ep('/analytics/scatter','Analytics','Paired sample x vs y','ListResponse',[qp('x',required=True),qp('y',required=True),qp('start_hst'),qp('end_hst')])
ep('/clusters','Machine Learning','Daily cluster assignments','ListResponse',[qp('model_version'),qp('start_hst'),qp('end_hst')])
ep('/predictions/latest','Machine Learning','Latest next-day regression prediction','Prediction',[qp('target_metric',default='WVHT')])
ep('/predictions/history','Machine Learning','Issued regression predictions','ListResponse',[qp('start_hst'),qp('end_hst')])
ep('/classifications/latest','Machine Learning','Latest next-day classification','ListResponse')
ep('/classifications/schemes','Machine Learning','Versioned classification thresholds','ListResponse')
ep('/models','Machine Learning','Registry entries (no model object URLs)','ListResponse',[qp('task_type',enum=['regression','classification','clustering']),qp('status')])
ep('/models/{model_id}/evaluations','Machine Learning','Saved validation/test metrics','ListResponse',[{'name':'model_id','in':'path','required':True,'schema':{'type':'integer'}},qp('stage',enum=['train','validation','test','walk_forward','backtest','live'])])
ep('/data-quality/overview','Monitoring','NOAA gaps QC and daily completeness','ListResponse',[qp('start_hst'),qp('end_hst')])
ep('/jobs/{processing_run_id}','Monitoring','Internal or admin job status','Job',[{'name':'processing_run_id','in':'path','required':True,'schema':{'type':'integer'}}])

p=Path(__file__).resolve().parents[1]/'docs'/'API_OPENAPI_DRAFT.yaml'
p.write_text('# DRAFT ONLY: not yet implemented, schema placeholders for list items.\n'+yaml.safe_dump(base,allow_unicode=True,sort_keys=False),encoding='utf-8')
print(p)
