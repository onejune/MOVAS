#########################################################################
# File Name: query_tracking_data.sh
# Author: onejune
# mail: onejune@126.com
# Created Time: Tue 26 Apr 2022 07:46:22 AM CST
#########################################################################
#!/bin/bash

export HADOOP_HOME=/data/hadoop-home/hdp-hadoop-3.1.1
export SPARK_HOME=/data2/hadoop-home/spark-3.1.1-bin-free-c59d19df39
export SPARK_CONF_DIR=/data2/hadoop-config/command-home/spark-k8s-offline-development-scientist-a-3.1/conf
export LANG="en_US.UTF-8"

function spark_submit_job() {
    sql=$1
    tag=$2
    output=$3
    hive_out="s3://mob-emr-test/wanjun/hive_out/temp_temp/$tag"
    if [ -z "$sql" ] || [ -z "$tag" ] || [ -z "$output" ]; then
        echo "ERROR: input is valid, exit !"
        exit
    fi
    hadoop fs -rmr $hive_out
    echo "sql: $sql"

    spark-submit \
    --deploy-mode cluster \
    --num-executors 400 \
    --executor-cores 2 \
    --driver-cores 4 \
    --driver-memory 12G \
    --executor-memory 6G \
    --conf spark.dynamicAllocation.enabled=true \
    --conf spark.dynamicAllocation.minExecutors=10 \
    --conf spark.dynamicAllocation.maxExecutors=500 \
    --conf spark.core.connection.ack.wait.timeout=600 \
    --class org.mobvista.dataplatform.SubmitSparkSql s3://mob-emr/adn/k8s_spark_migrate/release/spark-sql-submitter-1.0.0.jar \
    "${sql}"  \
    "${hive_out}"  \
    "1" \
    Overwrite \
    csv \
    "," \
    true

    hadoop fs -text $hive_out/* > $output
}

function query_ici_from_tracking_sdk() {
    beg_date=$1
    end_date=$2
	output=$3

    key="concat_ws('\t',concat(yyyy,mm,dd,hh), unit_id, country_code, ad_type, platform, get_json_object(ext_dsp,'$.is_hb'), split(strategy, '\\;')[0], ext_campaignpackagename)"
    key="concat_ws('\t',concat(yyyy,mm,dd), ad_type, platform, get_json_object(ext_dsp,'$.is_hb'), split(strategy, '\\;')[0], ext_campaignpackagename)"
    #key="concat_ws('\t',concat(yyyy,mm,dd), unit_id, country_code)"

    imp_sql="select 
        $key as key,
        count (*) as imp,
        sum(get_json_object(ext_dsp,'$.price_out')/1000/100) as cost
        from dwh.ods_adn_trackingnew_impression
        where (concat(yyyy,mm,dd,hh) >= "${beg_date}" and concat(yyyy,mm,dd,hh) <= "${end_date}")
        and strategy like '%MNormalAlpha%' 
        and ad_type in('rewarded_video','interstitial_video')
        group by
        $key"
	cost_sql="select
		$key as key,
		sum(expect_cost)/1000 as cost
		from dwh.ods_adn_trackingnew_ssp_pv
		where (concat(yyyy,mm,dd,hh) >= "${beg_date}" and concat(yyyy,mm,dd,hh) <= "${end_date}")
		and strategy like '%MNormalAlpha%' 
		group by
		$key"
    clk_sql="select 
        $key as key,
        count (*) as clk
        from dwh.ods_adn_trackingnew_click
        where (concat(yyyy,mm,dd,hh) >= "${beg_date}" and concat(yyyy,mm,dd,hh) <= "${end_date}")
        and strategy like '%MNormalAlpha%'  
        group by
        $key"
    ins_sql="select 
        $key as key,
        count (*) as ins,
        sum(if(size(split(ext_bp, '\"')) > 1, split(ext_bp, '\"')[1], split(substr(ext_bp, 2), ',')[0])) as rev
        from dwh.ods_adn_trackingnew_install
        where (concat(yyyy,mm,dd,hh) >= "${beg_date}" and concat(yyyy,mm,dd,hh) <= "${end_date}")
        and strategy like '%MNormalAlpha%' 
        group by
        $key"

    sql="select 
            a.key,
            a.imp, 
            b.clk, 
            c.ins,
            c.rev,
            d.cost
        from 
            ($imp_sql) a 
            left join ($clk_sql) b on a.key = b.key
            left join ($ins_sql) c on a.key = c.key
			left join ($cost_sql) d on a.key = d.key
        ;"
    echo "$sql"
    tag="query_ici_data"
    spark_submit_job "$sql" $tag $output
}

function query_ici_from_tracking_sdk2() {
    beg_date=$1
    end_date=$2
    output=$3
	need_request=$4

	dtm="concat(yyyy,mm,dd)"
	req_wf_key="concat_ws('\t',$dtm, ad_type, platform, 'null', if(split(strategy, '\\;')[0]='MNormalAlphaModelRankerHH', 'NRS', 'SRS'), 'null', 'null')"
	req_hb_key="concat_ws('\t',$dtm, ad_type, platform, 'null', if(split(extra3, '\\;')[0]='MNormalAlphaModelRankerHH', 'NRS', 'SRS'), 'null', 'null')"

    #key="concat_ws('\t',concat(yyyy,mm,dd), unit_id, country_code, ad_type, platform, get_json_object(ext_dsp,'$.is_hb'), split(strategy, '\\;')[0], ext_campaignpackagename, ext_finalpackagename)"
    #key="concat_ws('\t',concat(yyyy,mm,dd), ad_type, platform, get_json_object(ext_dsp,'$.is_hb'), split(strategy, '\\;')[0], ext_campaignpackagename)"
	key="concat_ws('\t',$dtm, ad_type, platform, 'null', if(split(strategy, '\\;')[0]='MNormalAlphaModelRankerHH', 'NRS', 'SRS'), ext_campaignpackagename, ext_finalpackagename)"
    #key="concat_ws('\t',concat(yyyy,mm,dd), unit_id, country_code)"

	req_wf_sql="select
		$req_wf_key as key,
		count(distinct requestid) as req,
		0 as imp, 0 as ins, 0 as rev, 0 as cost, 
		sum(split(ext_algo, ',')[3]) as cr_ivr,
		sum(split(split(ext_algo, ',')[29], '\004')[0]) as algo_price
		from dwh.ods_adn_trackingnew_request
		where (concat(yyyy,mm,dd,hh) >= "${beg_date}" and concat(yyyy,mm,dd,hh) <= "${end_date}")
		and strategy like '%MNormalAlpha%'
		and ad_type in('rewarded_video','interstitial_video')
		group by $req_wf_key
		"
	req_hb_sql="select
		$req_hb_key as key,
		count(distinct bidid) as req,
		0 as imp, 0 as ins, 0 as rev, 0 as cost,
		sum(split(ext_algo, ',')[3]) as cr_ivr,
		sum(split(split(ext_algo, ',')[29], '\004')[0]) as algo_price
		from dwh.ods_adn_hb_v1_bid
		where (concat(yyyy,mm,dd,hh) >= "${beg_date}" and concat(yyyy,mm,dd,hh) <= "${end_date}")
		and extra3 like '%MNormalAlpha%'
		and ad_type in('rewarded_video','interstitial_video')
		group by $req_hb_key
		"

    imp_sql="select 
        $key as key, requestid,
		split(split(ext_algo, ',')[29], '\004')[0] as algo_price,
		split(ext_algo, ',')[3] as cr_ivr
        from dwh.ods_adn_trackingnew_impression
        where (concat(yyyy,mm,dd,hh) >= "${beg_date}" and concat(yyyy,mm,dd,hh) <= "${end_date}")
        and strategy like '%MNormalAlpha%' 
        and ad_type in('rewarded_video','interstitial_video')
        "
    cost_sql="select
        requestid,
        expect_cost/1000 as cost
        from dwh.ods_adn_trackingnew_ssp_pv
        where (concat(yyyy,mm,dd,hh) >= "${beg_date}" and concat(yyyy,mm,dd,hh) <= "${end_date}")
        and strategy like '%MNormalAlpha%' 
        "
    clk_sql="select 
        $key as key,
        count (*) as clk
        from dwh.ods_adn_trackingnew_click
        where (concat(yyyy,mm,dd,hh) >= "${beg_date}" and concat(yyyy,mm,dd,hh) <= "${end_date}")
        and strategy like '%MNormalAlpha%'  
        group by
        $key
		"
    ins_sql="select 
        $key as key,
        count (*) as ins,
        sum(if(size(split(ext_bp, '\"')) > 1, split(ext_bp, '\"')[1], split(substr(ext_bp, 2), ',')[0])) as rev
        from dwh.ods_adn_trackingnew_install
        where (concat(yyyy,mm,dd,hh) >= "${beg_date}" and concat(yyyy,mm,dd,hh) <= "${end_date}")
        and strategy like '%MNormalAlpha%' 
        group by
        $key"

    imp_cost_join="
        select 
            ic.key as key,
            count(*) as imp,
            sum(ic.cost) as cost,
			sum(ic.cr_ivr) as cr_ivr,
			sum(ic.algo_price) as algo_price
        from
        (
            select 
            imp.key as key,
            cost.cost as cost,
			imp.cr_ivr as cr_ivr,
			imp.algo_price as algo_price
            from
            ($imp_sql) imp left join ($cost_sql) cost on imp.requestid = cost.requestid
        ) ic
        group by ic.key"

    sql="select 
            a.key as key,
			0 as req,
            a.imp as imp, 
            c.ins as ins,
            c.rev as rev,
            a.cost as cost,
			a.cr_ivr as cr_ivr,
			a.algo_price
        from 
            ($imp_cost_join) a
            left join ($ins_sql) c on a.key = c.key
        ;"
    
	echo "$sql"
    tag="query_ici_data"
    spark_submit_job "$sql" $tag $output &

	if [ $need_request -eq 1 ]; then
		echo "$req_wf_sql"
		tag="query_wf_req"
		output_wf="./output/wf_req.dat"
		spark_submit_job "$req_wf_sql" $tag $output_wf &
		
		echo "$req_hb_sql"
		tag="query_hb_req"
		output_hb="./output/hb_req.dat"
		spark_submit_job "$req_hb_sql" $tag $output_hb &

		wait
		cat $output_wf $output_hb >> $output
	else
		wait
	fi
}
#query_ici_from_tracking_adx $1 $2 $3
query_ici_from_tracking_sdk2 $1 $2 $3



