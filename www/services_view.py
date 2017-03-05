# -*- coding: utf8 -*-
import logging
import json
from decimal import Decimal

from django.views.decorators.cache import never_cache
from django.template.response import TemplateResponse
from django.shortcuts import redirect
from django.http import Http404

from share.manager.region_provier import RegionProviderManager
from www.models.main import TenantRegionPayModel, ServiceGroupRelation, ServiceCreateStep, ServiceAttachInfo, \
    TenantConsumeDetail, ServiceConsume, ServiceFeeBill
from www.views import BaseView, AuthedView, LeftSideBarMixin
from www.decorator import perm_required
from www.models import (Users, ServiceInfo, TenantRegionInfo, TenantServiceInfo,
                        ServiceDomain, PermRelService, PermRelTenant,
                        TenantServiceRelation, TenantServicesPort, TenantServiceEnv,
                        TenantServiceEnvVar, TenantServiceMountRelation,
                        ServiceExtendMethod, TenantServiceVolume)
from www.region import RegionInfo
from service_http import RegionServiceApi
from django.conf import settings
from goodrain_web.custom_config import custom_config
from www.tenantservice.baseservice import BaseTenantService, TenantUsedResource, TenantAccountService, CodeRepositoriesService
from www.monitorservice.monitorhook import MonitorHook
from www.utils.url import get_redirect_url
from www.utils.md5Util import md5fun
import datetime
import www.utils.sn as sn

logger = logging.getLogger('default')
regionClient = RegionServiceApi()
monitorhook = MonitorHook()
tenantAccountService = TenantAccountService()
baseService = BaseTenantService()
tenantUsedResource = TenantUsedResource()
codeRepositoriesService = CodeRepositoriesService()
rpmManager = RegionProviderManager()

class TenantServiceAll(LeftSideBarMixin, AuthedView):

    def get_media(self):
        media = super(TenantServiceAll, self).get_media() + self.vendor(
            'www/css/owl.carousel.css', 'www/css/goodrainstyle.css',
            'www/js/jquery.cookie.js', 'www/js/service.js', 'www/js/common-scripts.js', 'www/js/jquery.dcjqaccordion.2.7.js',
            'www/js/jquery.scrollTo.min.js')
        return media

    def check_region(self):
        region = self.request.GET.get('region', None)
        if region is not None:
            if region in RegionInfo.region_names():
                if region == 'aws-bj-1' and self.tenant.region != 'aws-bj-1':
                    raise Http404
                self.response_region = region
            else:
                raise Http404

        if self.cookie_region == 'aws-bj-1':
            self.response_region == 'ali-sh'

        try:
            t_region, created = TenantRegionInfo.objects.get_or_create(tenant_id=self.tenant.tenant_id, region_name=self.response_region)
            self.tenant_region = t_region
        except Exception, e:
            logger.error(e)

    @never_cache
    @perm_required('tenant.tenant_access')
    def get(self, request, *args, **kwargs):
        self.check_region()
        context = self.get_context()
        try:
            self.response_tenant_name = self.tenant
            logger.debug('monitor.user', str(self.user.pk))
            tenantServiceList = baseService.get_service_list(self.tenant.pk, self.user, self.tenant.tenant_id, region=self.response_region)

            # 获取组和服务的关系
            sgrs = ServiceGroupRelation.objects.filter(tenant_id=self.tenant.tenant_id,
                                                       region_name=self.response_region)
            serviceGroupIdMap = {}
            for sgr in sgrs:
                serviceGroupIdMap[sgr.service_id] = sgr.group_id
            context["serviceGroupIdMap"] = serviceGroupIdMap

            serviceGroupNameMap = {}
            group_list = context["groupList"]
            for group in group_list:
                serviceGroupNameMap[group.ID] = group.group_name
            context["serviceGroupNameMap"] = serviceGroupNameMap

            sorted_service_list = []
            unsorted_service_list = []
            for tenant_service in tenantServiceList:
                group_id = serviceGroupIdMap.get(tenant_service.service_id, None)
                if group_id is None:
                    group_id = -1
                group_name = serviceGroupNameMap.get(group_id, None)
                if group_name is None:
                    group_name = "未分组"
                tenant_service.group_name = group_name
                if tenant_service.group_name == "未分组":
                    unsorted_service_list.append(tenant_service)
                else:
                    sorted_service_list.append(tenant_service)

            context["sorted_service_list"] = sorted(sorted_service_list, key=lambda service: (service.group_name,service.service_cname))
            context["unsorted_service_list"] =sorted( unsorted_service_list ,key=lambda service:service.service_cname)
            context["totalAppStatus"] = "active"
            context["totalFlow"] = 0
            context["totalAppNumber"] = len(tenantServiceList)
            context["tenantName"] = self.tenantName
            totalNum = PermRelTenant.objects.filter(tenant_id=self.tenant.ID).count()
            context["totalNum"] = totalNum
            context["curTenant"] = self.tenant
            context["tenant_balance"] = self.tenant.balance
            # params for prompt
            context["pay_type"] = self.tenant.pay_type
            # context["expired"] = tenantAccountService.isExpired(self.tenant,self.service)
            context["expired_time"] = self.tenant.expired_time
            status = tenantAccountService.get_monthly_payment(self.tenant, self.tenant.region)
            context["monthly_payment_status"] = status
            if status != 0:
                list = TenantRegionPayModel.objects.filter(tenant_id=self.tenant.tenant_id, region_name=self.tenant.region).order_by("-buy_end_time")
                context["buy_end_time"] = list[0].buy_end_time

            if self.tenant_region.service_status == 0:
                logger.debug("tenant.pause", "unpause tenant_id=" + self.tenant_region.tenant_id)
                regionClient.unpause(self.response_region, self.tenant_region.tenant_id)
                self.tenant_region.service_status = 1
                self.tenant_region.save()
            elif self.tenant_region.service_status == 3:
                logger.debug("tenant.pause", "system unpause tenant_id=" + self.tenant_region.tenant_id)
                regionClient.systemUnpause(self.response_region, self.tenant_region.tenant_id)
                self.tenant_region.service_status = 1
                self.tenant_region.save()


        except Exception as e:
            logger.exception(e)
        return TemplateResponse(self.request, "www/service_my.html", context)


class TenantService(LeftSideBarMixin, AuthedView):

    def init_request(self, *args, **kwargs):
        fr = self.request.GET.get('fr', None)
        if fr is not None and fr == 'statistic':
            self.statistic = True
            self.statistic_type = self.request.GET.get('type', 'history')
        else:
            self.statistic = False

    def get_media(self):
        media = super(TenantService, self).get_media() + self.vendor(
            'www/assets/jquery-easy-pie-chart/jquery.easy-pie-chart.css',
            'www/css/owl.carousel.css', 'www/css/goodrainstyle.css', 'www/css/style.css',
            'www/css/bootstrap-switch.min.css', 'www/css/bootstrap-editable.css',
            'www/css/style-responsive.css', 'www/js/jquery.cookie.js', 'www/js/service.js',
            'www/js/gr/basic.js', 'www/css/gr/basic.css', 'www/js/perms.js',
            'www/js/common-scripts.js', 'www/js/jquery.dcjqaccordion.2.7.js', 'www/js/jquery.scrollTo.min.js',
            'www/js/swfobject.js', 'www/js/web_socket.js', 'www/js/websoket-goodrain.js',
            'www/js/bootstrap-switch.min.js', 'www/js/bootstrap-editable.min.js', 'www/js/gr/multi_port.js'
        )
        if self.statistic:
            if self.statistic_type == 'history':
                append_media = (
                    'www/assets/nvd3/nv.d3.css', 'www/assets/nvd3/d3.min.js',
                    'www/assets/nvd3/nv.d3.min.js', 'www/js/gr/nvd3graph.js',
                )
            elif self.statistic_type == 'realtime':
                append_media = ('www/js/gr/ws_top.js',)
            media = media + self.vendor(*append_media)
        return media

    def get_context(self):
        context = super(TenantService, self).get_context()
        return context

    def get_user_perms(self):
        perm_users = []
        perm_template = {
            'name': None,
            'adminCheck': False,
            'developerCheck': False,
            'developerDisable': False,
            'viewerCheck': False,
            'viewerDisable': False,
        }
        identities = PermRelService.objects.filter(service_id=self.service.pk)
        user_id_list = [x.user_id for x in identities]
        user_list = Users.objects.filter(pk__in=user_id_list)
        user_map = {x.user_id: x for x in user_list}

        for i in identities:
            user_perm = perm_template.copy()
            user_info = user_map.get(i.user_id)
            if user_info is None:
                continue
            user_perm['name'] = user_info.nick_name
            if i.user_id == self.user.user_id:
                user_perm['selfuser'] = True
            user_perm['email'] = user_info.email
            if i.identity == 'admin':
                user_perm.update({
                    'adminCheck': True,
                    'developerCheck': True,
                    'developerDisable': True,
                    'viewerCheck': True,
                    'viewerDisable': True,
                })
            elif i.identity == 'developer':
                user_perm.update({
                    'developerCheck': True,
                    'viewerCheck': True,
                    'viewerDisable': True,
                })
            elif i.identity == 'viewer':
                user_perm.update({'viewerCheck': True, 'viewerDisable': True})

            perm_users.append(user_perm)

        return perm_users

    def get_manage_app(self, http_port_str):
        service_manager = {"deployed": False}
        if self.service.service_key == 'mysql':
            has_managers = TenantServiceInfo.objects.filter(
                tenant_id=self.tenant.tenant_id, service_region=self.service.service_region, service_key='phpmyadmin')
            if has_managers:
                service_manager['deployed'] = True
                manager = has_managers[0]
                service_manager[
                    'url'] = 'http://{0}.{1}{2}:{3}'.format(manager.service_alias, self.tenant.tenant_name, settings.WILD_DOMAINS[self.service.service_region], http_port_str)
            else:
                # 根据服务版本获取对应phpmyadmin版本,暂时解决方法,待优化
                app_version = '4.4.12'
                if self.service.version == "5.6.30":
                    app_version = '4.6.3'
                service_manager['url'] = '/apps/{0}/service-deploy/?service_key=phpmyadmin&app_version={1}'.format(self.tenant.tenant_name, app_version)
        return service_manager

    def memory_choices(self):
        memory_dict = {}
        memory_dict["128"] = '128M'
        memory_dict["256"] = '256M'
        memory_dict["512"] = '512M'
        memory_dict["1024"] = '1G'
        memory_dict["2048"] = '2G'
        memory_dict["4096"] = '4G'
        memory_dict["8192"] = '8G'
        memory_dict["16384"] = '16G'
        memory_dict["32768"] = '32G'
        memory_dict["65536"] = '64G'
        return memory_dict

    def extends_choices(self):
        extends_dict = {}
        extends_dict["state"] = u'有状态'
        extends_dict["stateless"] = u'无状态'
        extends_dict["state-expend"] = u'有状态可水平扩容'
        return extends_dict

    # 端口开放下拉列表选项
    def multi_port_choices(self):
        multi_port = {}
        multi_port["one_outer"] = u'单一端口开放'
        # multi_port["dif_protocol"] = u'按协议开放'
        multi_port["multi_outer"] = u'多端口开放'
        return multi_port

    # 服务挂载卷类型下拉列表选项
    def mnt_share_choices(self):
        mnt_share_type = {}
        mnt_share_type["shared"] = u'共享'
        # mnt_share_type["exclusive"] = u'独享'
        return mnt_share_type

    # 获取所有的开放的http对外端口
    def get_outer_service_port(self):
        out_service_port_list = TenantServicesPort.objects.filter(service_id=self.service.service_id, is_outer_service=True, protocol='http')
        return out_service_port_list

    def generate_service_attach_info(self):
        """为先前的服务创建服务附加信息"""
        service_attach_info = ServiceAttachInfo()
        service_attach_info.tenant_id = self.tenant.tenant_id
        service_attach_info.service_id = self.service.service_id
        service_attach_info.memory_pay_method = "postpaid"
        service_attach_info.disk_pay_method = "postpaid"
        service_attach_info.min_memory = self.service.min_memory
        service_attach_info.min_node = self.service.min_node
        service_attach_info.disk = 0
        service_attach_info.pre_paid_period = 0
        service_attach_info.pre_paid_money = 0
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:00:00")
        service_attach_info.buy_start_time = datetime.datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
        service_attach_info.buy_end_time = datetime.datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
        service_attach_info.create_time = datetime.datetime.now()
        service_attach_info.save()
        return service_attach_info

    @never_cache
    @perm_required('view_service')
    def get(self, request, *args, **kwargs):
        self.response_region = self.service.service_region
        self.tenant_region = TenantRegionInfo.objects.get(tenant_id=self.service.tenant_id, region_name=self.service.service_region)
        context = self.get_context()
        context["tenantName"] = self.tenantName
        context['serviceAlias'] = self.serviceAlias
        fr = request.GET.get("fr", "deployed")
        context["fr"] = fr
        # 判断是否社区版云帮
        context["community"] = False
        if sn.instance.is_private():
            context["community"] = True
        try:
            regionBo = rpmManager.get_work_region_by_name(self.response_region)
            memory_post_paid_price = regionBo.memory_trial_price  # 内存按需使用价格
            disk_post_paid_price = regionBo.disk_trial_price  # 磁盘按需使用价格
            net_post_paid_price = regionBo.net_trial_price  # 网络按需使用价格
            if self.service.category == "application":
                # forbidden blank page
                if self.service.code_version is None or self.service.code_version == "" or (self.service.git_project_id == 0 and self.service.git_url is None):
                    codeRepositoriesService.initRepositories(self.tenant, self.user, self.service, "gitlab_new", "", "", "master")
                    self.service = TenantServiceInfo.objects.get(service_id=self.service.service_id)

                if ServiceCreateStep.objects.filter(service_id=self.service.service_id,
                                                    tenant_id=self.tenant.tenant_id).count() > 0:
                    app_step = ServiceCreateStep.objects.get(service_id=self.service.service_id,
                                                             tenant_id=self.tenant.tenant_id).app_step
                    logger.debug("create service step" + str(app_step))
                    if app_step == 2:
                        codeRepositoriesService.codeCheck(self.service)
                        return self.redirect_to('/apps/{0}/{1}/app-waiting/'.format(self.tenant.tenant_name, self.service.service_alias))
                    if app_step == 3:
                        return self.redirect_to('/apps/{0}/{1}/app-setting/'.format(self.tenant.tenant_name, self.service.service_alias))
                    if app_step == 4:
                        return self.redirect_to('/apps/{0}/{1}/app-language/'.format(self.tenant.tenant_name, self.service.service_alias))
            elif self.service.category == 'app_publish':
                # 市场安装
                if ServiceCreateStep.objects.filter(service_id=self.service.service_id,
                                                    tenant_id=self.tenant.tenant_id).count() > 0:
                    return self.redirect_to('/apps/{0}/{1}/deploy/setting/'.format(self.tenantName, self.serviceAlias))

            # service_consume_detail_list = TenantConsumeDetail.objects.filter(tenant_id=self.tenant.tenant_id,
            #                                                                  service_id=self.service.service_id).order_by("-ID")
            # last_hour_cost = None
            # if len(service_consume_detail_list) > 0:
            #     last_hour_cost = service_consume_detail_list[0]
            # if last_hour_cost is not None:
            #     last_hour_cost.memory_fee = round(
            #         last_hour_cost.memory / 1024 * memory_post_paid_price * last_hour_cost.node_num, 2)
            #     last_hour_cost.disk_fee = round(last_hour_cost.disk / 1024 * disk_post_paid_price, 2)
            #     last_hour_cost.net_fee = round(last_hour_cost.net / 1024 * net_post_paid_price, 2)
            #     last_hour_cost.total_fee = last_hour_cost.disk_fee + last_hour_cost.memory_fee + last_hour_cost.net_fee
            #     context["last_hour_cost"] = last_hour_cost
            context['is_tenant_free'] = (self.tenant.pay_type == "free")

            context["tenantServiceInfo"] = self.service
            context["myAppStatus"] = "active"
            context["perm_users"] = self.get_user_perms()
            context["totalMemory"] = self.service.min_node * self.service.min_memory
            context["tenant"] = self.tenant
            context["region_name"] = self.service.service_region
            context["websocket_uri"] = settings.WEBSOCKET_URL[self.service.service_region]
            context["wild_domain"] = settings.WILD_DOMAINS[self.service.service_region]
            if ServiceGroupRelation.objects.filter(service_id=self.service.service_id).count() > 0:
                gid = ServiceGroupRelation.objects.get(service_id=self.service.service_id).group_id
                context["group_id"] = gid
            else:
                context["group_id"] = -1
            service_domain = False
            if TenantServicesPort.objects.filter(service_id=self.service.service_id, is_outer_service=True, protocol='http').exists():
                context["hasHttpServices"] = True
                service_domain = True

            http_port_str = settings.WILD_PORTS[self.response_region]
            context['http_port_str'] = ":" + http_port_str

            service_attach_info = None
            try:
                service_attach_info = ServiceAttachInfo.objects.get(tenant_id=self.tenant.tenant_id,
                                                                    service_id=self.service.service_id)
            except ServiceAttachInfo.DoesNotExist:
                pass
            if service_attach_info is None:
                service_attach_info = self.generate_service_attach_info()

            now = datetime.datetime.now()
            if service_attach_info.buy_end_time < now:
                if service_attach_info.memory_pay_method == "prepaid" or service_attach_info.disk_pay_method == "prepaid":
                    service_attach_info.disk_pay_method = "postpaid"
                    service_attach_info.memory_pay_method = "postpaid"
                    service_attach_info.save()

            if fr == "deployed":
                if self.service.service_type == 'mysql':
                    service_manager = self.get_manage_app(http_port_str)
                    context['service_manager'] = service_manager

                # inner service
                innerPorts = {}
                tsps = TenantServicesPort.objects.filter(service_id=self.service.service_id, is_inner_service=True)
                for tsp in tsps:
                    innerPorts[tsp.container_port] = True

                if len(tsps) > 0:
                    context["hasInnerServices"] = True

                envMap = {}
                envVarlist = TenantServiceEnvVar.objects.filter(service_id=self.service.service_id, scope__in=("outer", "both"), is_change=False)
                if len(envVarlist) > 0:
                    for evnVarObj in envVarlist:
                        arr = envMap.get(evnVarObj.service_id)
                        if arr is None:
                            arr = []
                        arr.append(evnVarObj)
                        envMap[evnVarObj.service_id] = arr
                context["envMap"] = envMap

                containerPortList = []
                opend_service_port_list = TenantServicesPort.objects.filter(service_id=self.service.service_id, is_inner_service=True)
                if len(opend_service_port_list) > 0:
                    for opend_service_port in opend_service_port_list:
                        containerPortList.append(opend_service_port.container_port)
                context["containerPortList"] = containerPortList
                
                if self.service.code_from != "image_manual":
                    baseservice = ServiceInfo.objects.get(service_key=self.service.service_key, version=self.service.version)
                    if baseservice.update_version != self.service.update_version:
                        context["updateService"] = True

                context["docker_console"] = settings.MODULES["Docker_Console"]
                context["publish_service"] = settings.MODULES["Publish_Service"]

                # get port type
                context["visit_port_type"] = self.service.port_type
                if self.service.port_type == "multi_outer":
                    context["http_outer_service_ports"] = self.get_outer_service_port()

                service_consume_list = ServiceConsume.objects.filter(tenant_id=self.tenant.tenant_id,service_id=self.service.service_id).order_by("-ID")
                last_hour_cost = None
                if service_consume_list:
                    last_hour_cost = service_consume_list[0]
                context["last_hour_cost"] = last_hour_cost

            # 处理付费状态
                service_attach_info = ServiceAttachInfo.objects.get(service_id=self.service.service_id,
                                                                    tenant_id=self.tenant.tenant_id)
                buy_start_time = service_attach_info.buy_start_time
                buy_end_time = service_attach_info.buy_end_time
                memory_pay_method = service_attach_info.memory_pay_method
                disk_pay_method = service_attach_info.disk_pay_method
                is_package_servie = False
                if memory_pay_method == "prepaid" or disk_pay_method == "prepaid":
                    is_package_servie = True
                tips = "暂无"
                current_status = "未知"
                can_pay = False
                now = datetime.datetime.now()
                diff_minutes = int((buy_start_time - now).total_seconds() / 60)
                need_to_pay = 0.00
                body = regionClient.check_service_status(self.service.service_region, self.service.service_id)
                status = body[self.service.service_id]
                # status = "running"
                service_fee_bill_list = ServiceFeeBill.objects.filter(service_id=self.service.service_id,tenant_id=self.tenant.tenant_id,pay_status="unpayed")
                service_fee_bill = None
                if service_fee_bill_list:
                    service_fee_bill = service_fee_bill_list[0]
                prepaid_money = 0.00
                if service_fee_bill:
                    prepaid_money = service_fee_bill.prepaid_money
                start_time_str = buy_start_time.strftime("%Y-%m-%d %H:%M:%S")

                if is_package_servie:
                    if diff_minutes > 0:
                        if status != "running":
                            current_status = "调试中"
                            tips = "应用未运行"
                        elif prepaid_money > 0:
                            can_pay = True
                            need_to_pay = prepaid_money
                            current_status = "等待支付"
                            tips = "请于{0}前付款{1}元".format(start_time_str,str(prepaid_money))
                        else:
                            current_status = "即将计费"
                            tips = "将于{0}开始计费".format(start_time_str)
                    else:
                        if prepaid_money > 0 and status != "running":
                            current_status = "到期关闭"
                            tips = "请在费用页延期,或更改计费方式然后重启应用"
                        elif status != "running" and self.tenant.balance < 0:
                            current_status = "欠费关闭"
                            tips = "请为账户充值然后重启应用"
                        else:
                            if buy_end_time > now:
                                tips = "预付费将于{0}到期".format(buy_end_time.strftime("%Y-%m-%d %H:%M:%S"))
                                if last_hour_cost:
                                    current_status = str(last_hour_cost.pay_money)+"元"
                                else:
                                    current_status = "0元"
                            else:
                                tips = "当前应用所有项目均按小时计算"
                                if last_hour_cost:
                                    current_status = str(last_hour_cost.pay_money)+"元"
                                else:
                                    current_status = "0元"
                else:
                    if diff_minutes > 0:
                        if status != "running":
                            current_status = "调试中"
                            tips = "应用未运行"
                        else:
                            if diff_minutes < 15:
                                current_status = "即将计费"
                                tips = "将于{0}开始计费".format(start_time_str)
                            else:
                                tips = "当前应用所有项目均按小时计算"
                                if last_hour_cost:
                                    current_status = str(last_hour_cost.pay_money)+"元"
                                else:
                                    current_status = "0元"
                    else:
                        if status != "running" and self.tenant.balance < 0:
                            current_status = "欠费关闭"
                            tips = "请为账户充值然后重启应用"
                        else:
                            tips = "当前应用所有项目均按小时计算"
                            if last_hour_cost:
                                current_status = str(last_hour_cost.pay_money)+"元"
                            else:
                                current_status = "0元"
                context["tips"] = tips
                context["current_status"] = current_status
                context["can_pay"] = can_pay
                context["need_to_pay"] = need_to_pay
            elif fr == "relations":
                # service relationships
                tsrs = TenantServiceRelation.objects.filter(service_id=self.service.service_id)
                relationsids = []
                if len(tsrs) > 0:
                    for tsr in tsrs:
                        relationsids.append(tsr.dep_service_id)
                context["serviceIds"] = relationsids
                # service map
                map = {}
                sids = [self.service.service_id]
                tenantServiceList = baseService.get_service_list(self.tenant.pk, self.user, self.tenant.tenant_id, region=self.response_region)
                for tenantService in tenantServiceList:
                    if TenantServicesPort.objects.filter(service_id=tenantService.service_id, is_inner_service=True).exists():
                        sids.append(tenantService.service_id)
                        map[tenantService.service_id] = tenantService
                    if tenantService.service_id in relationsids:
                        map[tenantService.service_id] = tenantService
                context["serviceMap"] = map
                # env map
                envMap = {}
                envVarlist = TenantServiceEnvVar.objects.filter(service_id__in=sids, scope__in=("outer", "both"))
                for evnVarObj in envVarlist:
                    arr = envMap.get(evnVarObj.service_id)
                    if arr is None:
                        arr = []
                    arr.append(evnVarObj)
                    envMap[evnVarObj.service_id] = arr
                context["envMap"] = envMap

                # 当前服务的连接信息
                currentEnvMap = {}
                currentEnvVarlist = TenantServiceEnvVar.objects.filter(service_id=self.service.service_id, scope__in=("outer", "both"),is_change=False)
                if len(currentEnvVarlist) > 0:
                    for evnVarObj in currentEnvVarlist:
                        arr = currentEnvMap.get(evnVarObj.service_id)
                        if arr is None:
                            arr = []
                        arr.append(evnVarObj)
                        currentEnvMap[evnVarObj.service_id] = arr
                context["currentEnvMap"] = currentEnvMap

                containerPortList = []
                opend_service_port_list = TenantServicesPort.objects.filter(service_id=self.service.service_id, is_inner_service=True)
                if len(opend_service_port_list) > 0:
                    for opend_service_port in opend_service_port_list:
                        containerPortList.append(opend_service_port.container_port)
                context["containerPortList"] = containerPortList

                # add dir mnt
                mtsrs = TenantServiceMountRelation.objects.filter(service_id=self.service.service_id)
                mntsids = []
                if len(mtsrs) > 0:
                    for mnt in mtsrs:
                        mntsids.append(mnt.dep_service_id)
                context["mntsids"] = mntsids

            elif fr == "statistic":
                context['statistic_type'] = self.statistic_type
                if self.service.service_type in ('mysql',):
                    context['ws_topic'] = '{0}.{1}.statistic'.format(''.join(list(self.tenant.tenant_id)[1::2]), ''.join(list(self.service.service_id)[::2]))
                else:
                    #context['ws_topic'] = '{0}.{1}.statistic'.format(self.tenant.tenant_name, self.service.service_alias)
                    if self.service.port_type=="multi_outer":
                        tsps = TenantServicesPort.objects.filter(service_id=self.service.service_id, is_outer_service=True)
                        for tsp in tsps:
                            context['ws_topic'] = '{0}.{1}_{2}.statistic'.format(self.tenant.tenant_name, self.service.service_alias, str(tsp.container_port))
                    else:
                        context['ws_topic'] = '{0}.{1}.statistic'.format(self.tenant.tenant_name, self.service.service_alias)
                service_port_list = TenantServicesPort.objects.filter(tenant_id=self.tenant.tenant_id,
                                                                      service_id=self.service.service_id)
                has_outer_port = False
                for p in service_port_list:
                    if (p.is_outer_service and p.protocol == "http") or self.service.service_type == 'mysql':
                        has_outer_port = True
                        break
                context["has_outer_port"] = has_outer_port
            elif fr == "log":
                pass
            elif fr == "settings":
                nodeList = []
                memoryList = []
                try:
                    sem = ServiceExtendMethod.objects.get(service_key=self.service.service_key, app_version=self.service.version)
                    nodeList.append(sem.min_node)
                    next_node = sem.min_node + sem.step_node
                    while(next_node <= sem.max_node):
                        nodeList.append(next_node)
                        next_node = next_node + sem.step_node

                    num = 1
                    memoryList.append(str(sem.min_memory))
                    next_memory = sem.min_memory * pow(2, num)
                    while(next_memory <= sem.max_memory):
                        memoryList.append(str(next_memory))
                        num = num + 1
                        next_memory = sem.min_memory * pow(2, num)
                except Exception as e:
                    nodeList.append(1)
                    memoryList.append(str(self.service.min_memory))
                    memoryList.append("1024")
                    memoryList.append("2048")
                    memoryList.append("4096")
                    memoryList.append("8192")

                context["nodeList"] = nodeList
                context["memoryList"] = memoryList
                context["memorydict"] = self.memory_choices()
                context["extends_choices"] = self.extends_choices()
                context["add_port"] = settings.MODULES["Add_Port"]
                if custom_config.GITLAB_SERVICE_API:
                    context["git_tag"] = True
                else:
                    context["git_tag"] = False
                context["mnt_share_choices"] = self.mnt_share_choices()
                context["http_outer_service_ports"] = self.get_outer_service_port()
                # service git repository
                try:
                    context["httpGitUrl"] = codeRepositoriesService.showGitUrl(self.service)
                    if self.service.code_from == "gitlab_manual":
                        href_url = self.service.git_url
                        if href_url.startswith('git@'):
                            href_url = "http://" + href_url.replace(":", "/")[4:]
                        context["href_url"] = href_url
                except Exception as e:
                    pass
                if service_domain:
                    # service domain
                    # domain_num = ServiceDomain.objects.filter(service_id=self.service.service_id).count()
                    # if domain_num == 1:
                    #     domain = ServiceDomain.objects.get(service_id=self.service.service_id)
                    #     context["serviceDomain"] = domain
                    serviceDomainlist = ServiceDomain.objects.filter(service_id=self.service.service_id)
                    if len(serviceDomainlist) > 0:
                        data = {}
                        for domain in serviceDomainlist:
                            if data.get(domain.container_port) is None:
                                data[domain.container_port] = [domain.domain_name]
                            else:
                                data[domain.container_port].append(domain.domain_name)
                        context["serviceDomainDict"] = data

                port_list = TenantServicesPort.objects.filter(service_id=self.service.service_id)
                outer_port_exist = False
                if len(port_list) > 0:
                    outer_port_exist = reduce(lambda x, y: x or y, [t.is_outer_service for t in list(port_list)])
                context["ports"] = list(port_list)
                context["outer_port_exist"] = outer_port_exist
                # 付费用户或者免费用户的mysql,免费用户的docker
                context["outer_auth"] = self.tenant.pay_type != "free" or self.service.service_type == 'mysql' or self.service.language == "docker"
                # 付费用户,管理员的application类型服务可以修改port
                context["port_auth"] = (self.tenant.pay_type != "free" or self.user.is_sys_admin) and self.service.service_type == "application"
                context["envs"] = TenantServiceEnvVar.objects.filter(service_id=self.service.service_id, scope__in=("inner", "both")).exclude(container_port=-1)

                # 获取挂载信息,查询
                volume_list = TenantServiceVolume.objects.filter(service_id=self.service.service_id)
                # result_list = []
                # for volume in list(volume_list):
                #     tmp_path = volume.volume_path
                # if tmp_path:
                #     volume.volume_path = tmp_path.replace("/app", "", 1)
                # result_list.append(volume)
                context["volume_list"] = volume_list

                if self.service.code_from is not None and self.service.code_from in ("image_manual"):
                    context["show_git"] = False
                else:
                    context["show_git"] = True

                # 获取组和服务的关系
                sgrs = ServiceGroupRelation.objects.filter(tenant_id=self.tenant.tenant_id, region_name=self.response_region)
                serviceGroupIdMap = {}
                for sgr in sgrs:
                    serviceGroupIdMap[sgr.service_id] = sgr.group_id
                context["serviceGroupIdMap"] = serviceGroupIdMap

                serviceGroupNameMap = {}
                group_list = context["groupList"]
                for group in group_list:
                    serviceGroupNameMap[group.ID] = group.group_name
                context["serviceGroupNameMap"] = serviceGroupNameMap

            elif fr == "cost":
                # service_attach_info = None
                # try:
                #     service_attach_info = ServiceAttachInfo.objects.get(tenant_id=self.tenant.tenant_id,
                #                                                         service_id=self.service.service_id)
                # except ServiceAttachInfo.DoesNotExist:
                #     pass
                # if service_attach_info is None:
                #     service_attach_info = self.generate_service_attach_info()

                context["service_attach_info"] = service_attach_info
                context["service"] = self.service
                service_consume_list = ServiceConsume.objects.filter(tenant_id=self.tenant.tenant_id, service_id=self.service.service_id).order_by("-ID")
                last_hour_consume = None
                if len(list(service_consume_list)) > 0:
                    last_hour_consume = list(service_consume_list)[0]
                context["last_hour_consume"] = last_hour_consume
                service_total_memory_fee = Decimal(0)
                service_total_disk_fee = Decimal(0)
                service_total_net_fee = Decimal(0)
                consume_list = list(service_consume_list)
                for service_consume in consume_list:
                    service_consume.memory_pay_method = "postpaid"
                    service_consume.disk_pay_method = "postpaid"
                    if service_attach_info.buy_start_time <= service_consume.time and service_consume.time <= service_attach_info.buy_end_time:
                        if service_attach_info.memory_pay_method == "prepaid":
                            service_consume.memory_pay_method = "prepaid"
                        if service_attach_info.disk_pay_method == "prepaid":
                            service_consume.disk_pay_method = "prepaid"
                    service_total_memory_fee += service_consume.memory_money
                    service_total_disk_fee += service_consume.disk_money
                    service_total_net_fee += service_consume.net_money

                context["service_consume_list"] = consume_list
                context["service_total_memory_fee"] = service_total_memory_fee
                context["service_total_disk_fee"] = service_total_disk_fee
                context["service_total_net_fee"] = service_total_net_fee


            else:
                return self.redirect_to('/apps/{0}/{1}/detail/'.format(self.tenant.tenant_name, self.service.service_alias))

            if self.tenant_region.service_status == 0:
                logger.debug("tenant.pause", "unpause tenant_id=" + self.tenant_region.tenant_id)
                regionClient.unpause(self.service.service_region, self.tenant_region.tenant_id)
                self.tenant_region.service_status = 1
                self.tenant_region.save()

            elif self.tenant_region.service_status == 3:
                logger.debug("tenant.pause", "system unpause tenant_id=" + self.tenant_region.tenant_id)
                regionClient.systemUnpause(self.service.service_region, self.tenant_region.tenant_id)
                self.tenant_region.service_status = 1
                self.tenant_region.save()
        except Exception as e:
            logger.exception(e)
        return TemplateResponse(self.request, "www/service_detail.html", context)


class ServiceGitHub(BaseView):

    @never_cache
    def get(self, request, *args, **kwargs):
        code = request.GET.get("code", "")
        state = request.GET.get("state", "")
        if code != "" and state != "" and int(state) == self.user.pk:
            result = codeRepositoriesService.get_gitHub_access_token(code)
            content = json.loads(result)
            token = content["access_token"]
            user = Users.objects.get(user_id=int(state))
            user.github_token = token
            user.save()
        tenantName = request.session.get("app_tenant")
        logger.debug(tenantName)
        return self.redirect_to("/apps/" + tenantName + "/app-create/?type=github")


class ServiceLatestLog(AuthedView):

    def get_media(self):
        media = super(ServiceLatestLog, self).get_media() + self.vendor('www/css/owl.carousel.css', 'www/css/goodrainstyle.css',
                                                                        'www/js/jquery.cookie.js', 'www/js/common-scripts.js', 'www/js/jquery.dcjqaccordion.2.7.js', 'www/js/jquery.scrollTo.min.js')
        return media

    @never_cache
    def get(self, request, *args, **kwargs):
        try:
            context = self.get_context()
            data = {}
            data['number'] = 1000
            body = regionClient.latest_log(self.service.service_region, self.service.service_id, json.dumps(data))
            context["lines"] = body["lines"]
        except Exception as e:
            logger.exception(e)
        return TemplateResponse(self.request, "www/service_docker_log.html", context)


class ServiceHistoryLog(AuthedView):

    def get_media(self):
        media = super(ServiceHistoryLog, self).get_media() + self.vendor('www/css/owl.carousel.css', 'www/css/goodrainstyle.css',
                                                                         'www/js/jquery.cookie.js', 'www/js/common-scripts.js', 'www/js/jquery.dcjqaccordion.2.7.js', 'www/js/jquery.scrollTo.min.js')
        return media

    @never_cache
    def get(self, request, *args, **kwargs):
        try:
            context = self.get_context()
            body = regionClient.history_log(self.service.service_region, self.service.service_id)
            context["log_paths"] = body["log_path"]
            context["tenantService"] = self.service
            context["log_domain"] = settings.LOG_DOMAIN[self.service.service_region]
        except Exception as e:
            logger.exception(e)
        return TemplateResponse(self.request, "www/service_history_log.html", context)


class ServiceDockerContainer(AuthedView):

    def get_media(self):
        media = super(ServiceDockerContainer, self).get_media()
        return media

    @never_cache
    def get(self, request, *args, **kwargs):
        context = self.get_context()
        response = redirect(get_redirect_url("/apps/{0}/{1}/detail/".format(self.tenantName, self.serviceAlias), request))
        try:
            docker_c_id = request.COOKIES.get('docker_c_id', '')
            docker_h_id = request.COOKIES.get('docker_h_id', '')
            docker_s_id = request.COOKIES.get('docker_s_id', '')
            if docker_c_id != "" and docker_h_id != "" and docker_s_id != "" and docker_s_id == self.service.service_id:
                t_docker_h_id = docker_h_id.lower()
                context["tenant_id"] = self.service.tenant_id
                context["service_id"] = docker_s_id
                context["ctn_id"] = docker_c_id
                context["host_id"] = t_docker_h_id
                context["md5"] = md5fun(self.service.tenant_id + "_" + docker_s_id + "_" + docker_c_id)
                pro = settings.DOCKER_WSS_URL.get("type", "ws")
                if pro == "ws":
                    context["wss"] = pro + "://" + settings.DOCKER_WSS_URL[self.service.service_region] + "/ws?nodename=" + t_docker_h_id
                else:
                    context["wss"] = pro + "://" + settings.DOCKER_WSS_URL[self.service.service_region] + "/ws?nodename=" + t_docker_h_id

                response = TemplateResponse(self.request, "www/console.html", context)
            response.delete_cookie('docker_c_id')
            response.delete_cookie('docker_h_id')
            response.delete_cookie('docker_s_id')
        except Exception as e:
            logger.exception(e)
        return response
