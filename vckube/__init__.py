#!/usr/bin/env python3
# coding=utf-8
"""
Cluster management tool for setting up a coreos-vagrant cluster
"""
from __future__ import division, print_function, absolute_import, unicode_literals

import os
import re
import json
import stat
import time
import pickle
import shutil
import socket
import vagrant
import zipfile
import platform
import netifaces
import subprocess
import concurrent.futures

from os import path
from tempfile import NamedTemporaryFile
from arguments import Use, abort, Schema, abspath, BaseArguments, delete_directory
from cmdssh import shell, cmd_run, scp_run, cmd_exec, download, remote_cmd, invoke_shell, remote_cmd_map, CallCommandException
from consoleprinter import Info, info, console, doinput, warning, clear_screen, query_yes_no, console_warning, console_exception, colorize_for_print, console_error_exit


class VagrantArguments(BaseArguments):
    """
    MainArguments
    """
    def __init__(self, parent=None):
        """
        @type parent: str, None
        @return: None
        """
        self.force = False
        self.__workingdir = None
        self.args = []
        self.localizemachine = None
        self.reload = None
        self.replacecloudconfig = None
        self.commandline = None
        self.command = ""
        self.createproject = None
        self.parallel = False
        self.wait = 0
        self.projectname = ""
        doc = """
            Vagrant cluster management by Active8 NL
            erik@a8.nl

            Usage:
                vckube [options] [--] <command> [<projectname>] [<args>...]

            Options:
                -h --help               Show this screen.
                -p --parallel           Execute commands in parallel, default is serial execution
                -v --verbose            Verbose mode.
                -f --force              Do not ask for confirmation
                -w --wait=<ws>          Wait <ws> seconds between commands.
                -d --workingdir=<wrkd>  Directory to execute commands in, default is current working dir.

            Commands:
                ansible        Provision cluster with ansible-playbook(s) [(<labelservers>:<nameplaybook>) ..]
                baseprovision  Apply configuration, createproject calls this.
                createproject  Create a Coreos Kubernetes cluster in local directory
                destroy        Destroy vagrant cluster (vagrant destroy -f)
                halt           Halt vagrant cluster (vagrant halt)
                kubectl        kubectl command [kubectl [<args>...]]
                reboot         Reboot the cluster
                reset          Reset cloudconfig settings and replace on cluster, reboots cluster
                restartvmware  Start or restart vmware
                ssh            Make ssh connection into specific machine
                sshcmd         Execute command on cluster (remote command)
                status         Status of cluster or machine
                up             Bring cluster up

        """
        self.validcommands = ['ansible', 'baseprovision', 'coreostoken', 'createproject', 'destroy', 'halt', 'kubectl', 'reload', 'reset', 'ssh', 'sshcmd', 'status', 'reboot', 'restartvmware', 'up']
        validateschema = Schema({'command': Use(self.validcommand)})
        self.set_command_help("up", "Start all vm's in the cluster")
        self.set_command_help("status", "ssh-config data combined with other data")
        self.set_command_help("ansible", "example: cbx ansible myproject all:myplaybook.yml core1:anotherplaybook.yml")
        self.set_command_help("kubectl", "commands for the kubectl binary [<help|create|get|version|delete|args>..]")

        super(VagrantArguments, self).__init__(doc=doc, validateschema=validateschema, parent=parent)

    @property
    def workingdir(self):
        """
        @return: str
        """
        if self.__workingdir is None:
            return None

        return str(self.__workingdir)

    @workingdir.setter
    def workingdir(self, v):
        """
        @type v: str
        """
        if self.workingdir is not None:
            raise AssertionError("workingdir was already set", self.workingdir)
        else:
            if v is not None:
                self.__workingdir = os.path.abspath(v)


def bool_to_text(inputbool):
    """
    @type inputbool: bool
    @return: None
    """
    if inputbool is True:
        return "\033[32myes\033[0m"
    else:
        return "\033[31mno\033[0m"


def cat(fpath, mode="rt"):
    """
    @type fpath: str
    @type mode: str
    @return: None
    """
    with open(fpath, mode) as f:
        return f.read()


def cmd_ansible(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    playbook = None
    server = None
    password = None

    for serverplaybook in commandline.args:
        if ":" not in serverplaybook:
            serverplaybook = "all:" + serverplaybook

        spb = serverplaybook.split(":")

        if len(spb) == 2:
            playbook = os.path.abspath(os.path.expanduser(spb[1]))
            server = spb[0].strip()
        elif len(spb) == 2:
            playbook = os.path.abspath(os.path.expanduser(spb[1]))
            server = spb[0].strip()
            password = spb[2].strip()

    if playbook and os.path.exists(playbook):
        info(commandline.command, "playbook found at " + playbook)
    else:
        warning(commandline.command, "no playbook found at " + playbook)

    if server is None:
        abort(commandline.command, "server is None")
    elif playbook is None:
        abort(commandline.command, "playbook is None")

    cmd_provision_ansible(server, playbook, password)


def cmd_baseprovision(commandline, provider):
    """
    @type commandline: VagrantArguments
    @type provider: str
    @return: None
    """
    try:
        shell("ssh-add keys/insecure/vagrant")
    except BaseException as ex:
        console(ex)

    info(commandline.command, "make directories on server")
    cmd_up(commandline, provider)
    cmd_remote_command("sudo mkdir /root/pypy&&sudo ln -s /home/core/bin /root/pypy/bin;", commandline.parallel, keypath=get_keypaths())
    info(commandline.command, "install python container on server")
    cmd_provision_ansible("all", "./playbooks/ansiblebootstrap.yml", None)
    keypath = os.path.join(os.getcwd(), "keys/secure/vagrantsecure")
    generate_keypair(commandline.command, "core user vagrant", keypath)
    cmd_provision_ansible("all", "./playbooks/keyswap.yml", None)
    cmd_reset(commandline, commandline.wait)


def cmd_connect_ssh(server):
    """
    @type server: str
    @return: None
    """
    cnt = 0
    vmnames = get_vm_names()
    index = 1

    if server not in vmnames:
        try:
            index = int(server)
        except ValueError:
            index = None
    try:
        cmd_run("ssh-add keys/secure/vagrantsecure")
    except BaseException as ex:
        console(ex)
    try:
        cmd_run("ssh-add keys/insecure/vagrant")
    except BaseException as ex:
        console(ex)

    if server == "all" or server not in vmnames and index is not None:
        for name in vmnames:
            cnt += 1

            if server == "all" or index == cnt:
                info("ssh", name)

                while True:
                    try:
                        try:
                            rv = shell("ssh core@" + name + ".a8.nl")

                            if rv == 255:
                                info(name, "waiting")
                                time.sleep(1)
                            else:
                                if rv != 0:
                                    info("exit", rv)
                                break
                        except BaseException as ex:
                            warning(name, str(ex))
                            if invoke_shell(name + ".a8.nl", "core", get_keypaths()) != 0:
                                print("connection lost, trying in 1 seconds (ctrl-c to quit)")
                                time.sleep(1)
                            else:
                                break

                    except KeyboardInterrupt:
                        info("connect_ssh", "bye")
                        break

                if server != 'all':
                    break
    else:
        if server in vmnames:
            cmd = "vagrant ssh " + server
            shell(cmd)
        else:
            if server != 'all':
                warning("ssh", "server " + server + " not found, options are:")
                answers = []

                for name in vmnames:
                    answers.append(name)

                inputserver = doinput("enter name or number:", answers=answers)
                cmd = "vagrant ssh " + inputserver
                shell(cmd)


def pickle_save(commandline, name, o):
    """
    @type commandline: VagrantArguments
    @type name: str
    @type o: dict, list, int, float
    @return: None
    """
    info(commandline.command, "saving pickle " + str(name))
    picklepath = os.path.join(str(commandline.workingdir), ".vckube")
    ppath = os.path.join(picklepath, name)
    dirname = os.path.dirname(ppath)

    if len(dirname) > 0:
        if not os.path.exists(dirname):
            os.makedirs(dirname, exist_ok=True)

    pickle.dump(o, open(ppath, "wb"))


def pickle_load(commandline, name):
    """
    @type commandline: VagrantArguments
    @type name: str
    @return: dict, list, int, float
    """
    info(commandline.command, "saving pickle " + str(name))
    picklepath = os.path.join(str(commandline.workingdir), ".vckube")
    ppath = os.path.join(picklepath, name)

    if not os.path.exists(ppath):
        raise FileExistsError(ppath)

    return pickle.load(open(ppath, "rb"))


def cmd_createproject(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    gui, numinstance, memory, numcpu, name, deletefiles = input_vagrant_parameters(commandline)
    ensure_project_folder(commandline, name, deletefiles)
    commandline = set_working_dir(commandline, name)
    savedata = {"gui": gui,
                "numinstance": numinstance,
                "memory": memory,
                "numcpu": numcpu,
                "name": name,
                "deletefiles": deletefiles,
                "commandline.workingdir": commandline.workingdir}

    pickle_save(commandline, "vmdata", savedata)
    download_and_unzip_vckuberant_project(commandline)
    configure_generic_cluster_files_for_this_machine(commandline, gui, numinstance, memory, numcpu)
    cmd_run("vagrant box update")
    readytoboot = True

    if readytoboot:
        provider = get_provider()
        set_gateway_and_coreostoken(commandline)
        cmd_up(commandline, provider)


def cmd_createproject_driver(commandline, name, project_found):
    """
    @type commandline: VagrantArguments
    @type name: str
    @type project_found: str
    @return: None
    """
    if project_found:
        abort(commandline.command, "project file exist [" + str(name) + "], refusing overwrite")
    try:
        cmd_createproject(commandline)
        cmd_run("vagrant halt")
    except BaseException as be:
        abort(commandline.command, str(be))
    try_cnt = 0
    while try_cnt < 5:
        try:
            info(commandline.command, "bring up attempt " + str(try_cnt + 1))
            cmd_run("vagrant up")
            try_cnt = 5
            break
        except CallCommandException as cce:
            try_cnt += 1
            print(cce)


def cmd_destroy_vagrant_cluster():
    """
    destroy_vagrant_cluster
    """
    cwd = os.getcwd()

    try:
        cmd = "vagrant destroy -f"
        cmd_run(cmd)

        for vmx in str(os.popen("vmrun list")):
            if ".vmx" in vmx:
                vmx = vmx.strip()
                cmd_run("vmrun stop " + vmx + " > /dev/null &")
                cmd_run("vmrun deleteVM " + vmx + " > /dev/null &")

    finally:
        os.chdir(os.path.dirname(cwd))
        shutil.rmtree(cwd)


def cmd_driver_vagrant(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    if hasattr(commandline, "help") and commandsline.help is True:
        return

    if len(commandline.args) == 0:
        if commandline.workingdir:
            commandline.args.append(commandline.projectname)

    if not commandline.command:
        raise AssertionError("no command set")

    project_found, name = get_working_directory(commandline)
    project_displayname = "Vagrant Kubernetes Cluster"

    if not project_found and commandline.command != "createproject" and commandline.command != "restartvmware":
        abort(commandline.command, "A vckube environment is required.Run 'vckube createproject' or \nchange to a directory with a 'Vagrantfile' and '.cl' folder in it.")
    else:
        if commandline.command not in ["createproject"]:
            project_displayname += ": \033[34m" + os.path.dirname(os.getcwd()) + "/" + name + "\033[0m"

    if commandline.command == "status":
        clear_screen()

    console(project_displayname, plaintext=True, color="green")

    if commandline.wait and commandline.wait > 10 and commandline.force is False:
        if query_yes_no("wait is very large (" + str(commandline.wait) + ") continue", default=True) is False:
            return

    if commandline.command == "createproject":
        cmd_createproject_driver(commandline, name, project_found)
        cmd_baseprovision(commandline, get_provider())
    elif commandline.command == "up":
        provider = get_provider()
        cmd_up(commandline, provider)
    elif commandline.command == "halt":
        cmd_run("vagrant halt")
    elif commandline.command == "kubectl":
        cmd_kubectl(commandline)
    elif commandline.command == "coreostoken":
        cmd_print_coreos_token_stdout()
    elif commandline.command == "destroy":
        cmd_destroy_vagrant_cluster()
    elif commandline.command == "reload":
        cmd_run("vagrant reload")
    elif commandline.command == "reboot":
        cmd_remote_command("sudo reboot", True, timeout=5, keypath=get_keypaths())
    elif commandline.command == "status":
        print("\033[91mcluster machines:\033[0m")
        cmd_statuscluster(commandline)
        print()
        print("\033[91mkubernetes system:\033[0m")
        commandline.args = ["get", "all"]
        cmd_kubectl(commandline)
    elif commandline.command == "reset":
        removelist = ["user-data" + str(x) + ".yml" for x in range(1, get_num_instances() + 1)]
        removelist.extend(["master.yml", "node.yml"])
        removelist = [os.path.join(os.path.join(commandline.workingdir, "configscripts"), x) for x in removelist]
        [os.remove(x) for x in removelist if os.path.exists(x)]
        set_gateway_and_coreostoken(commandline)
        cmd_reset(commandline, commandline.wait)
    elif commandline.command == "ansible":
        cmd_ansible(commandline)
    elif commandline.command == "baseprovision":
        provider = get_provider()
        cmd_baseprovision(commandline, provider)
        password = doinput("testansible password", default="")
        cmd_provision_ansible("all", "./playbooks/testansible.yml", password)
    elif commandline.command == "ssh":
        cmd_ssh(commandline)
    elif commandline.command == "sshcmd":
        cmd_sshcmd(commandline)
    elif commandline.command == "restartvmware":
        cmd_restart_vmware(commandline)
    else:
        abort(commandline.command, "not implemented")
        console(commandline)


def cmd_kubectl(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    machine = platform.machine()

    if "64" in str(machine):
        machine = "amd64"
    else:
        machine = "386"

    system = platform.system()
    kubectl = os.path.join(commandline.workingdir, "platforms")
    kubectl = os.path.join(kubectl, system.lower())
    kubectl = os.path.join(kubectl, machine)
    kubectl = os.path.join(kubectl, "kubectl")

    if not os.path.exists(kubectl):
        os.makedirs(os.path.dirname(kubectl), exist_ok=True)
        download("https://storage.googleapis.com/kubernetes-release/release/v0.15.0/bin/darwin/amd64/kubectl", kubectl)

    if not os.path.exists(kubectl):
        abort(commandline.command, "kubectl not found: " + str(kubectl))

    if not os.access(kubectl, os.X_OK):
        info("cmd_kubectl:chmod:exec", kubectl)
        os.chmod(kubectl, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)

    kubectl += " --server="
    vmhostosx = is_osx()

    if vmhostosx is True:
        kubectl += "http://core1.a8.nl:8080"
    else:
        kubectl += "http://node1.a8.nl:8080"

    kubectl += " "

    def filterkubectllog(s):
        """
        @type s: str
        @return: None
        """
        sp = s.split("]")

        if len(sp) > 1:
            return "]".join(sp[1:])
        else:
            return s

    if len(commandline.args) > 0:
        kubectlcmd = commandline.args[0].strip()
        execute = True
        kubectl += kubectlcmd + " "
        restargs = commandline.args[1:]

        if kubectlcmd == "create":
            if len(restargs) == 0:
                info("create options", "create FILENAME")
                execute = False

            kubectl += "-f "
            kubectl += " ".join(restargs)
        elif kubectlcmd == "update":
            if len(restargs) == 0:
                info("update options", "update FILENAME")
                execute = False

            kubectl += "-f "
            kubectl += " ".join(restargs)
        elif kubectlcmd == "get":
            if len(restargs) == 0:
                info("get options", "Possible resources include\n- pods (po)\n- replication controllers (rc)\n- services (svc)\n- minions (mi)\n- events (ev)")
                execute = False

            myall = False

            for i in restargs:
                resources = {'rc': 'replication controllers',
                             'mi': 'minions',
                             'po': 'pods',
                             'svc': 'services'}

                reskeys = sorted(resources.keys())

                if i == "all":
                    execute = False
                    myall = True

                    for cnt, k in enumerate(reskeys):
                        kubectl1 = kubectl + k
                        cmd_exec(kubectl1, cmdtoprint="\033[34m" + resources[k] + ":\033[0m", myfilter=filterkubectllog)

                        if cnt < len(reskeys) - 1:
                            print()

            if myall is False:
                kubectl += " ".join(restargs)

        elif kubectlcmd == "deleteall":
            kubectl += "delete services,replicationControllers,pods --all"
            kubectl = kubectl.replace("deleteall", "")
        elif kubectlcmd == "delete":
            kubectl += "delete "
            kubectl += " ".join(restargs)
        elif kubectlcmd == "version":
            execute = cmd_version(commandline, kubectl)

        if execute is True:
            code, _ = cmd_exec(kubectl, cmdtoprint=kubectl, myfilter=filterkubectllog)

            if code == 0:
                info(kubectlcmd, "ok")

    elif len(commandline.args) == 0:
        warning(commandline.command, "no arguments given, options:\n- create\n- delete\n- get\n- version")
    else:
        abort(commandline.command, "unknown command")


def cmd_print_coreos_token_stdout():
    """
    print_coreos_token_stdout
    """
    print("\033[36m" + str(get_token()) + "\033[0m")


def cmd_provision_ansible(targetvmname, playbook, password):
    """
    @type targetvmname: str
    @type playbook: str
    @type password: str, None
    @return: None
    """
    info("provision_ansible", targetvmname + ":" + playbook)
    f = NamedTemporaryFile(delete=False, mode="w+t")

    if password is not None:
        f.write(password)
        f.seek(0)

    info("ansible-playbook:", playbook)
    p = subprocess.Popen(["python", "-m", "SimpleHTTPServer", "8000"], stdout=open("/dev/null", "w"), stderr=open("/dev/null", "w"))
    try:
        if path.exists("./hosts"):
            vmnames = get_vm_names()

            if targetvmname == "all":
                cmd = "ansible-playbook -u core --inventory-file=" + path.join(os.getcwd(), "hosts") + "  -u core --limit=all " + playbook

                if password is not None:
                    cmd += " --vault-password-file " + f.name
                cmd_run(cmd, prefix=targetvmname + ":" + playbook)
            else:
                for vmname in vmnames:
                    if targetvmname == vmname:
                        info("provisioning", vmname)
                        cmd = "ansible-playbook -u core -i ./hosts  -u core --limit=" + vmname + " " + playbook

                        if password is not None:
                            cmd += " --vault-password-file " + f.name
                        cmd_run(cmd, prefix=targetvmname + ":" + playbook)
                    else:
                        info("skipping", vmname)
        else:
            cmd_run("vagrant provision")
    finally:
        p.kill()
        os.remove(f.name)


def cmd_remote_command(command, parallel, wait=0, server=None, timeout=60, keypath=None):
    """
    @type command: str
    @type parallel: bool
    @type wait: int
    @type server: None, str
    @type timeout: int
    @type keypath: None, str
    @return: None
    """
    cmdinfo = str(command)

    if server is None:
        serverinfo = "cluster"
    else:
        serverinfo = server

    if wait is None:
        wait = 0

    if parallel:
        cmdinfo += " in parallel"
        if wait != 0:
            wait = 0
            warning("wait", "parallel mode wait reset to 0")

    if wait > 0:
        serverinfo += " wait " + str(wait) + " second"
    elif wait < 0:
        serverinfo += " pausing for input"
    info(serverinfo, cmdinfo)

    if server is None:
        vmnames = get_vm_names()

        if command not in vmnames:
            commands = []

            for name in vmnames:
                cmd = command

                if parallel is True:
                    commands.append((name + '.a8.nl', cmd, 'core', keypath))
                else:
                    result = remote_cmd(name + '.a8.nl', cmd, timeout=timeout, username='core', keypath=keypath)

                    if result.strip():
                        cmd_remote_command_print_result(name, result)
                    else:
                        info(name, command)

                    if wait is not None:
                        if str(wait) == "-1":
                            try:
                                if query_yes_no("continue", default=True) is False:
                                    break
                                else:
                                    clear_screen()
                            except KeyboardInterrupt:
                                info(str(command), "sshcmd_remote_command:bye")
                                break
                        else:
                            time.sleep(float(wait))

            if len(commands) > 0:
                with concurrent.futures.ProcessPoolExecutor(max_workers=8) as executor:
                    result = executor.map(remote_cmd_map, commands)
                    lastoutput = ""

                    for server, result in result:
                        if result.strip():
                            lastoutput = cmd_remote_command_print_result(server.split(".")[0], result, lastoutput)
                        else:
                            warning(command, server.split(".")[0] + "... done")
    else:
        cmd = command
        result = remote_cmd(server + '.a8.nl', cmd, username='core', keypath=get_keypaths())

        if result:
            cmd_remote_command_print_result(server, result)


def cmd_remote_command_print_result(server, result, lastoutput=""):
    """
    @type server: str, unicode
    @type result: str, unicode
    @type lastoutput: str
    @return: None
    """
    result = result.strip()

    if "\n" in result:
        result = "\n" + result + "\n-"

    server = "results " + server
    if result != lastoutput:
        console(str(server), color="blue", plainprint=True, newline=False)
        print(result)
    else:
        console(str(server) + ":", "same", color="black", plainprint=True)

    return result


def cmd_reset(commandline, wait=None):
    """
    @type commandline: VagrantArguments
    @type wait: str, None
    @return: None
    """
    vmdata = pickle_load(commandline, "vmdata")
    vmhostosx = is_osx()
    write_new_tokens(vmhostosx)
    ntl = "configscripts/node.tmpl.yml"
    write_config_from_template(commandline, ntl, vmhostosx, vmdata["memory"], vmdata["numcpu"])
    ntl = "configscripts/master.tmpl.yml"
    write_config_from_template(commandline, ntl, vmhostosx, vmdata["memory"], vmdata["numcpu"])

    cmd_run("rm -f " + os.path.join(os.getcwd(), "./configscripts") + "/user-data*")
    info(commandline.command, "replace cloudconfiguration, checking vms are up")
    p = subprocess.Popen(["/usr/bin/vagrant", "up"], cwd=os.getcwd())
    p.wait()
    vmnames = get_vm_names()
    knownhosts = path.join(path.join(path.expanduser("~"), ".ssh"), "known_hosts")

    if path.exists(knownhosts):
        os.remove(knownhosts)

    if len(vmnames) > 0:
        cnt = 1

        for name in vmnames:
            info("reset", name + '.a8.nl put configscript')
            scp_run(server=name + '.a8.nl', cmdtype="put", fp1="configscripts/user-data" + str(cnt) + ".yml", fp2="/tmp/vagrantfile-user-data", username="core", keypath=get_keypaths())
            cmd = "sudo cp /tmp/vagrantfile-user-data /var/lib/coreos-vagrant/vagrantfile-user-data"
            remote_cmd(name + '.a8.nl', cmd, username='core', keypath=get_keypaths())
            info(name, "uploaded config rebooting now")
            logpath = path.join(os.getcwd(), "logs/" + name + "-serial.txt")

            if path.exists(path.dirname(logpath)):
                open(logpath, "w").write("")

            cmd = "sudo reboot"
            remote_cmd(name + '.a8.nl', cmd, username='core', keypath=get_keypaths())

            if wait is not None:
                if str(wait) == "-1":
                    try:
                        iquit = eval(input("\n\n---\npress enter to continue (q=quit): "))
                        if iquit.strip() == "q":
                            break

                        cmd_run("clear")
                    except KeyboardInterrupt:
                        info(commandline.command, "reset:bye")
                        break
                else:
                    time.sleep(float(wait))

            cnt += 1


def cmd_restart_vmware(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    osx = is_osx()

    for cnt in range(1, 15):
        try:
            if cnt > 2:
                info(commandline.command, "attempt " + str(cnt))

            if cnt > 4:
                if osx:
                    os.system("sudo vmnet-cli --stop")
                    time.sleep(1)
                    os.system("sudo vmnet-cli --start")
                    time.sleep(2)
                else:
                    os.system("sudo /usr/bin/vmware-networks --stop")
                    time.sleep(1)
                    os.system("sudo /usr/bin/vmware-networks --start")
                    time.sleep(2)

                if cnt > 6:
                    break
            else:
                if osx:
                    cmd_run("sudo vmnet-cli --stop")
                    time.sleep(1)
                    cmd_run("sudo vmnet-cli --start")
                    time.sleep(2)
                else:
                    cmd_run("sudo /usr/bin/vmware-networks --stop")
                    time.sleep(1)
                    cmd_run("sudo /usr/bin/vmware-networks --start")
                    time.sleep(2)
                break
        except CallCommandException as ex:
            warning(commandline.command, str(ex) + " attempt " + str(cnt))


def cmd_ssh(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    server = None
    if commandline.projectname is not None and len(commandline.args) != 1:
        server = "1"
    elif len(commandline.args) > 1 or len(commandline.args) == 0:
        abort(commandline.command, "No server given, [cbx vagrant ssh servername]")
    else:
        server = str(commandline.args[0])

    if server is not None:
        cmd_connect_ssh(server)


def cmd_sshcmd(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    cmd = None

    if len(commandline.args) == 0:
        warning(commandline.command, "no remote command entered [...vagrant <projectname> <sshcmd>]")
    else:
        cmd = commandline.args[0]
    try:
        server = None

        if ":" in cmd:
            cmds = cmd.split(":")
            server = cmds[0]
            cmd = cmds[1]

        cmd_remote_command(cmd, commandline.parallel, wait=commandline.wait, server=server, timeout=5, keypath=get_keypaths())
    except socket.timeout as ex:
        abort("sshcmd: " + commandline.args[0], "exception -> " + str(ex))


def cmd_statuscluster(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    vmnames = get_vm_names()

    if len(vmnames) > 0:
        for cnt, name in enumerate(vmnames):
            cmd = "vagrant ssh-config " + name
            try:
                out = ""

                if path.exists(".cl/" + name + ".statuscluster"):
                    out = open(".cl/" + name + ".statuscluster").read()

                if len(out.strip()) == 0:
                    out = cmd_run(cmd, streamoutput=False, returnoutput=True)
                    out = out.strip()
                    open(".cl/" + name + ".statuscluster", "wt").write(out)

                res = ""

                for row in out.split("\n"):
                    if "HostName" in row:
                        res = row.replace("HostName", "").strip()
                        res = "\033[31m" + res + "\033[0m"

                result = remote_cmd(name + '.a8.nl', 'cat /etc/os-release|grep VERSION_ID', username="core", keypath=get_keypaths())

                if len(result.strip()) > 0:
                    res = " ".join([name, res.strip()])
                    res = " ".join([res, "up", result.lower().strip()])
                    info(commandline.command, colorize_for_print(res))
                    print_ctl_cmd(name, "systemctl list-units | sed 's/loaded//' | sed 's/LOAD  //' | sed 's/[\d128-\d255]//g'", ["kube", "docker", "flannel", "etcd", "fleet", "setup-network-environment"])
                else:
                    print(colorize_for_print(name + " down"))

                if cnt < len(vmnames) - 1:
                    print()
            except subprocess.CalledProcessError as cpex:
                console_exception(cpex)
    else:
        cmd_run("vagrant status")


def cmd_up(commandline, provider):
    """
    @type commandline: VagrantArguments
    @type provider: str
    @return: None
    """
    curr_gw = get_default_gateway()
    config_gw = open("config/gateway.txt").read()
    if curr_gw.strip() != config_gw.strip():
        cmd_reset(commandline)

    if provider is None:
        raise AssertionError("provider is None")

    p = subprocess.Popen(["python", "-m", "SimpleHTTPServer", "8000"], stdout=open("/dev/null", "w"), stderr=open("/dev/null", "w"))
    try:
        cmd = "vagrant up --provider=" + provider
        cmd_run(cmd)
    finally:
        p.kill()


def cmd_version(commandline, kubectl):
    """
    @type commandline: VagrantArguments
    @type kubectl: str
    @return: None
    """
    ka = kubectl
    code, rv = cmd_exec(ka, display=False)
    if code != 0:
        abort(ka, code)

    if "version.Info" in rv:
        for rv in rv.split("\n"):
            rvs = rv.split("version.Info")
            jc = rvs[1]
            jc = jc.replace("{", "{'").replace(":", "':").replace(", ", ", '").replace("'", '"').strip().replace(" ", "")
            version = json.loads(jc)
            info(commandline.projectname + " " + rvs[0].lower().strip().strip(":"), version["GitVersion"])
    else:
        warning(ka, rv)

    ka = kubectl.strip().rstrip("version") + "apiversions"
    code, rv = cmd_exec(ka, display=False)
    if code != 0:
        abort(ka, code)

    rv = rv.split(":")

    if len(rv) > 1:
        info(rv[0].lower(), rv[1])
    else:
        warning(ka, rv)

    execute = False
    return execute


def configure_generic_cluster_files_for_this_machine(commandline, gui, numinstance, memory, numcpu):
    """
    @type commandline: VagrantArguments
    @type gui: int
    @type numinstance: int
    @type memory: int
    @type numcpu: int
    @return: None
    """
    if not hasattr(commandline, "workingdir"):
        console_warning("workingdir not set")

        raise SystemExit()

    if commandline.workingdir is None:
        console_warning("workingdir is None")

        raise SystemExit()

    os.chdir(str(commandline.workingdir))
    picklepath = os.path.join(str(commandline.workingdir), ".cl")

    if not os.path.exists(picklepath):
        os.mkdir(picklepath)

    vagrantfile = os.path.join(str(commandline.workingdir), "Vagrantfile")

    if not path.exists(vagrantfile + ".tpl.rb"):
        console_warning("no Vagrantfile in directory")

        raise SystemExit()

    if not path.exists(picklepath):
        os.mkdir(picklepath)

    func_extra_config = None
    vagranthome = commandline.workingdir
    mod_extra_config_path = path.join(str(vagranthome), "extra_config_vagrant.py")

    if os.path.exists(mod_extra_config_path):
        try:
            mod_extra_config = __import__(mod_extra_config_path)
            if mod_extra_config is not None:
                func_extra_config = mod_extra_config.__main__
        except ImportError:
            pass

    vmhost, provider = prepare_config(func_extra_config)
    info(commandline.command, provider)
    if commandline.command in ["createproject", "baseprovision", "reset", "reload", "command"]:
        vfp = open(vagrantfile)
        vf = vfp.read()

        vfp.close()
        vf = vf.replace("cpus = x", "cpus = " + str(numcpu))
        vf = vf.replace("cpus = x", "cpus = " + str(numcpu))
        vf = vf.replace("$num_instances = x", "$num_instances = " + str(numinstance))
        vf = vf.replace("$update_channel = 'beta'", "$update_channel = 'alpha'")
        vf = vf.replace("$vm_gui = x", "$vm_gui = " + str(gui).lower())
        vf = vf.replace("$vm_memory = x", "$vm_memory = " + str(memory))
        vf = vf.replace("$vm_cpus = x", "$vm_cpus = " + str(numcpu))

        open(vagrantfile, "w").write(vf)
        ntl = "configscripts/node.tmpl.yml"
        write_config_from_template(commandline, ntl, vmhost, memory, numcpu)
        ntl = "configscripts/master.tmpl.yml"
        write_config_from_template(commandline, ntl, vmhost, memory, numcpu)

    if False is localize_config(commandline, vmhost):
        raise AssertionError("localize_config was False")

    return provider, vmhost


def cp(fpathin, fpathout):
    """
    @type fpathin: str
    @type fpathout: str
    @return: None
    """
    shutil.copyfile(fpathin, fpathout)


def download_and_unzip_vckuberant_project(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    info(commandline.command, "downloading latest version of k8s/coreos for vagrant")
    zippath = os.path.join(os.getcwd(), "master.zip")
    zippathroot = os.path.join(os.path.dirname(os.getcwd()), "master.zip")

    if os.path.exists(zippathroot):
        info(commandline.command, "copy " + zippathroot + " -> " + zippath)
        shutil.copyfile(zippathroot, zippath)

    if not os.path.exists(zippath):
        for cnt in range(1, 4):
            try:
                download("https://github.com/erikdejonge/vckube-createproject/archive/master.zip", zippath)
                unzip("master.zip")
                break
            except zipfile.BadZipFile:
                if cnt > 5:
                    warning("badzipfile", "try again " + str(cnt))
    else:
        try:
            unzip("master.zip")
        except zipfile.BadZipFile as bze:
            console_exception(bze)
            try:
                download("https://github.com/erikdejonge/vckube-createproject/archive/master.zip", zippath)
                unzip("master.zip")
            except zipfile.BadZipFile as zex:
                console_exception(zex)
                console_warning("could not unzip clusterfiles", print_stack=True)

                raise SystemExit()


def echo(content, fpathout):
    """
    @type content: str
    @type fpathout: str
    @return: None
    """
    to_file(fpathout, content)


def ensure_project_folder(commandline, name, deletefiles):
    """
    @type commandline: VagrantArguments
    @type name: str
    @type deletefiles: bool
    @return: None
    """
    if not os.path.exists(name):
        info(commandline.command, "creating projectfolder: " + name)
        os.mkdir(name)
    elif not os.path.isdir(name):
        abort(commandline.command, "workdir path is file")

        raise SystemExit()
    elif not len(os.listdir(name)) == 0:
        if deletefiles is False:
            abort(commandline.command, "path not empty")

            raise SystemExit()
        else:
            delete_directory(name, [])

    if not len(os.listdir(name)) == 0:
        abort(commandline.command, "path not empty", stack=True)

        raise SystemExit()


def generate_keypair(cmdname, comment, privatekeypath):
    """
    @type cmdname: str
    @type comment: str
    @type privatekeypath: str
    @return: None
    """
    info(cmdname, "make key " + privatekeypath)

    if os.path.exists(privatekeypath):
        os.remove(privatekeypath)
        os.remove(privatekeypath + ".pub")

    cmd_run("ssh-keygen -t rsa -C \"" + comment + "\" -b 4096 -f ./" + os.path.basename(privatekeypath) + " -N \"\"", cwd=os.path.dirname(privatekeypath))


def get_default_gateway():
    """
    get_default_gateway
    """
    default_gateway = None
    gateways = netifaces.gateways()

    for gws in gateways:
        if gws == "default":
            for gw in gateways[gws]:
                for gw2 in gateways[gws][gw]:
                    if "." in gw2:
                        default_gateway = gw2

    return default_gateway


def get_keypaths():
    """
    get_keypaths
    """
    relp = ["keys/secure/vagrantsecure", "keys/insecure/vagrant"]
    pathrs = [os.path.join(os.getcwd(), x) for x in relp]
    paths = [x for x in pathrs if os.path.exists(x)]
    return paths


def get_num_instances():
    """
    get_num_instances
    """
    v = open("Vagrantfile").read()
    numinstances = int(v[v.find("num_instances") + (v[v.find("num_instances"):].find("=")):].split("\n")[0].replace("=", "").strip())
    return numinstances


def get_provider():
    """
    get_provider
    """
    if host_osx():
        provider = "vmware_fusion"
    else:
        provider = "vmware_workstation"

    return provider


def get_token():
    """
    get_token
    """
    token = os.popen("curl -s https://discovery.etcd.io/new?size=1").read()
    cnt = 0

    while "Unable" in token:
        if cnt > 3:
            raise AssertionError("could not fetch token")

        time.sleep(1)
        token = os.popen("curl -s https://discovery.etcd.io/new?size=3").read()
        cnt += 1

    return token


def get_vm_configs():
    """
    get_vm_configs
    """
    cwd = os.getcwd()
    picklepath = os.path.join(cwd, ".cl/vmnames.pickle")
    get_vm_names()
    result = [x[1] for x in pickle.load(open(picklepath)) if x[1] is not None]

    if len(result) > 0:
        return result
    else:
        v = vagrant.Vagrant()
        status = v.status()
        vmnames = []

        for vm in status:
            vmname = vm.name.split()[0].strip()
            vmnames.append([vmname, v.conf(v.ssh_config(vm_name=vmname))])

        if len(vmnames) > 0:
            picklepath = os.path.join(cwd, ".cl/vmnames.pickle")
            pickle.dump(vmnames, open(picklepath, "wb"))

        return [x[1] for x in vmnames if x[1] is not None]


def get_vm_names(retry=False):
    """
    @type retry: str, unicode
    @return: None
    """
    try:
        cwd = os.getcwd()
        cldir = os.path.join(cwd, ".cl")

        if not os.path.exists(cldir):
            os.mkdir(cldir)

        picklepath = os.path.join(cwd, ".cl/vmnames.pickle")

        if not os.path.exists(os.path.join(cwd, "Vagrantfile")):
            return []

        if path.exists(picklepath):
            l = sorted([x[0] for x in pickle.load(open(picklepath, "rb"))])
            return l

        vmnames = []
        numinstances = get_num_instances()
        osx = is_osx()

        for i in range(1, numinstances + 1):
            if osx is True:
                vmnames.append(["core" + str(i), None])
            else:
                vmnames.append(["node" + str(i), None])

        if numinstances is None:
            v = vagrant.Vagrant()
            status = v.status()

            for vm in status:
                vmname = vm.name.split()[0].strip()
                vmnames.append([vmname, v.conf(v.ssh_config(vm_name=vmname))])

        if len(vmnames) > 0:
            pickle.dump(vmnames, open(picklepath, "wb"))

        l = sorted([x[0] for x in vmnames])
        return l
    except subprocess.CalledProcessError as ex:
        print(ex)

        if retry:
            return []

        return get_vm_names(True)


def get_working_directory(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    tname = commandline.projectname
    retname = tname

    if tname is None:
        tname = os.path.basename(os.getcwd())

    if tname is not None:
        vagrantfile = os.path.join(os.path.join(os.path.dirname(os.getcwd()), str(tname)), "Vagrantfile")

        if os.path.exists(vagrantfile):
            retname = os.path.basename(os.path.dirname(vagrantfile))
            commandline.projectname = retname
            commandline.workingdir = os.path.dirname(vagrantfile)
        else:
            vagrantfile = os.path.join(os.path.join(os.getcwd(), str(tname)), "Vagrantfile")

            if os.path.exists(vagrantfile):
                retname = tname
                commandline.workingdir = os.path.dirname(vagrantfile)
            else:
                vagrantfile = os.path.join(os.getcwd(), "Vagrantfile")

                if os.path.exists(vagrantfile):
                    if len(commandline.m_argv) > 0:
                        commandline.args.insert(0, tname)

                    retname = os.path.basename(os.path.dirname(vagrantfile))
                    commandline.projectname = retname
                    commandline.workingdir = os.path.dirname(vagrantfile)

    if commandline.workingdir and not os.path.exists(commandline.workingdir):
        commandline.workingdir = None

    project_found = commandline.workingdir is not None
    if project_found is True:
        os.chdir(str(commandline.workingdir))
        retname = os.path.basename(str(commandline.workingdir))

    if retname is None:
        retname = "?"

    return project_found, retname


def host_osx():
    """
    host_osx
    """
    vmhostosx = False

    if str(os.popen("uname -a").read()).startswith("Darwin"):
        vmhostosx = True

    return vmhostosx


def input_vagrant_parameters(commandline, numcpus=8, gui=False, instances=3, memory=2048, confirmed=False, deleteoldfiles=False):
    """
    @type commandline: VagrantArguments
    @type numcpus : int
    @type gui : bool
    @type instances : int
    @type memory : int
    @type confirmed : bool
    @type deleteoldfiles : bool
    @return: None
    """
    name = commandline.projectname

    if commandline.force is False:
        while not confirmed:
            name = doinput("projectname", default=name, force=commandline.force)
            fp = os.path.join(os.getcwd(), name)

            if os.path.exists(fp):
                if len(os.listdir(fp)) > 0:
                    deleteoldfiles = query_yes_no(["force delete all files in directory:", fp], default=deleteoldfiles, force=commandline.force)

            numcpus = doinput("cpus per instance", default=numcpus, force=commandline.force)
            try:
                numcpus = int(numcpus)

                if numcpus < 2:
                    raise ValueError("too small")
            except ValueError as vax:
                warning(commandline.command, str(vax) + ", resetting to 4")
                numcpus = 4

            gui = not query_yes_no("headless", default=not gui, force=commandline.force)
            instances = doinput("clustersize", default=instances, force=commandline.force)
            try:
                instances = int(instances)
            except ValueError:
                warning(commandline.command, "instances input invalid, resetting to 2")
                instances = 2

            memory = doinput("memory per instance", default=memory, force=commandline.force)
            try:
                memory = int(memory)

                if memory < 1024:
                    raise ValueError("too small")
            except ValueError as vax:
                warning(commandline.command, str(vax) + ", resetting to 1024")
                instances = 1024

            print_config(commandline, deleteoldfiles, gui, instances, memory, name, numcpus)
            confirmed = query_yes_no("Is this ok", default=True, force=commandline.force)
    else:
        print_config(commandline, deleteoldfiles, gui, instances, memory, name, numcpus)

    return gui, instances, memory, numcpus, name, deleteoldfiles


def is_osx():
    """
    is_osx
    """
    osx = False

    if str(os.popen("uname -a").read()).startswith("Darwin"):
        osx = True

    return osx


def localize_config(commandline, vmhostosx):
    """
    @type commandline: VagrantArguments
    @type vmhostosx: bool
    @return: None
    """
    cmd_run('rm -Rf ".cl"')
    cmd_run('rm -Rf "hosts"')

    if not os.path.exists(".cl"):
        os.mkdir(".cl")

    if vmhostosx is True:
        info(commandline.command, "Localized for OSX")
    else:
        info(commandline.command, "Localized for Linux")

    hosts = open("hosts", "w")

    # for cf in get_vm_configs():
    # hosts.write(cf["Host"] + " ansible_ssh_host=" + cf["HostName"] + " ansible_ssh_port=22\n")
    vmnames = get_vm_names()

    for name in vmnames:
        try:
            hostip = str(socket.gethostbyname(name + ".a8.nl"))
            hosts.write(name + " ansible_ssh_host=" + hostip + " ansible_ssh_port=22\n")
        except socket.gaierror:
            hosts.write(name + " ansible_ssh_host=" + name + ".a8.nl ansible_ssh_port=22\n")

    hosts.write("\n[masters]\n")

    for name in vmnames:
        hosts.write(name + "\n")
        break

    cnt = 0
    hosts.write("\n[etcd]\n")

    for name in vmnames:
        if cnt == 1:
            hosts.write(name + "\n")

        cnt += 1

    cnt = 0
    hosts.write("\n[nodes]\n")

    for name in vmnames:
        if cnt > 0:
            hosts.write(name + "\n")

        cnt += 1

    hosts.write("\n[all]\n")

    for name in vmnames:
        hosts.write(name + "\n")

    hosts.write("\n[all_groups:children]\nmasters\netcd\nnodes\n")
    hosts.write("\n[coreos]\n")

    for name in vmnames:
        hosts.write(name + "\n")

    hosts.write("\n[coreos:vars]\n")
    hosts.write("ansible_ssh_user=core\n")
    hosts.write("ansible_python_interpreter=\"PATH=/home/core/bin:$PATH python\"\n")

    hosts.flush()
    hosts.close()
    cwd = os.getcwd()
    ntl = os.path.join(cwd, "configscripts/node.tmpl.yml")

    if not os.path.exists(ntl):
        console_error_exit("configscripts/node.tmpl.yml not found", print_stack=True)

    vmdata = pickle_load(commandline, "vmdata")
    write_config_from_template(commandline, ntl, vmhostosx, vmdata["memory"], vmdata["numcpu"])
    ntl = os.path.join(cwd, "configscripts/master.tmpl.yml")

    if not os.path.exists(ntl):
        console_error_exit("configscripts/master.tmpl.yml not found", print_stack=True)

    write_config_from_template(commandline, ntl, vmhostosx, vmdata["memory"], vmdata["numcpu"])
    return True


def main():
    """
    main
    """
    try:
        run_commandline()
    except KeyboardInterrupt:
        info("main", "bye")


def prepare_config(func_extra_config=None):
    """
    @type func_extra_config: str, unicode, None
    @return: None
    """
    vmhostosx = host_osx()

    if not os.path.exists("/config/tokenosx.txt") or not os.path.exists("/config/tokenlinux.txt"):
        write_new_tokens(vmhostosx)

    cp("Vagrantfile.tpl.rb", "Vagrantfile")

    if vmhostosx is True:
        provider = get_provider()
        cp("./roles/coreos-bootstrap/files/bootstraposx.txt", "./roles/coreos-bootstrap/files/bootstrap.sh")
        echo("192.168.14.4", "./config/startip.txt")
        echo("core", "./config/basehostname.txt")
        echo("f294d901-f14b-4370-9a43-ddb2cdb1ad02", "./config/updatetoken.txt")
        cp("./config/tokenosx.txt", "./config/token.txt")
        sed("node", "core", "Vagrantfile")
        sed("core.yml", "node.yml", "Vagrantfile")
    else:
        provider = get_provider()
        cp("./roles/coreos-bootstrap/files/bootstraplinux.txt", "./roles/coreos-bootstrap/files/bootstrap.sh")
        echo("192.168.14.5", "./config/startip.txt")
        echo("node", "./config/basehostname.txt")
        echo("3a1f12c5-de6a-4ca9-9357-579598038cd8", "./config/updatetoken.txt")
        cp("./config/tokenlinux.txt", "./config/token.txt")

    if func_extra_config:
        func_extra_config()

    if provider == "":
        console_error_exit("no provider set")

    retval = (vmhostosx, provider)
    return retval


def print_config(commandline, deleteoldfiles, gui, instances, memory, name, numcpus):
    """
    @type commandline: VagrantArguments
    @type deleteoldfiles: bool
    @type gui: bool
    @type instances: int
    @type memory: int
    @type name: str
    @type numcpus: int
    @return: None
    """
    print()

    with Info(commandline.command, "confirmation") as groupinfo:
        groupinfo.add("project", str(name))
        groupinfo.add("directory", str(os.path.join(os.getcwd(), name)))
        groupinfo.add("force project", deleteoldfiles)
        groupinfo.add("cpu's per instance", str(numcpus))
        groupinfo.add("headless gui", not gui)
        groupinfo.add("number of instances", str(instances))
        groupinfo.add("memory per instance", str(memory))


def print_ctl_cmd(name, systemcmd, shouldhaveword):
    """
    @type name: str
    @type systemcmd: str
    @type shouldhaveword: list
    @return: None
    """
    kunits = set()
    header = None

    for line in remote_cmd(name + '.a8.nl', systemcmd, "core", keypath=get_keypaths()).split("\n"):
        if header is None:
            header = line

        for word in line.split():
            for sw in shouldhaveword:
                if sw in word:
                    if ".service" in line:
                        kunits.add(line)
                        break

    kunits = list(kunits)
    kunits.sort(key=lambda xx: xx.split()[0])

    with Info(systemcmd) as groupinfo:
        for line in kunits:
            servicesplit = line.split(".service")
            service = [x.strip().replace("   ", " ") for x in servicesplit]
            groupinfo.add(service[0], " ".join(service[1:]))


def run_commandline(parent=None):
    """
    @type parent: Arguments, None
    @return: None
    """
    commandline = VagrantArguments(parent)
    cmd_driver_vagrant(commandline)


def sed(oldstr, newstr, infile):
    """
    @type oldstr: str
    @type newstr: str
    @type infile: str
    @return: None
    """
    linelist = []

    with open(infile) as f:
        for item in f:
            newitem = re.sub(oldstr, newstr, item)
            linelist.append(newitem)

    with open(infile, "w") as f:
        f.truncate()

        for line in linelist:
            f.writelines(line)


def set_gateway_and_coreostoken(commandline):
    """
    @type commandline: VagrantArguments
    @return: None
    """
    default_gateway = get_default_gateway()
    if default_gateway is None:
        warning(commandline.command, "default gateway could not be found")

        if query_yes_no("use 127"):
            to_file("config/gateway.txt", "127.0.0.1")
        else:
            abort("abort", "no gateway")
    else:
        info(commandline.command, "default gateway: " + default_gateway)
        to_file("config/gateway.txt", default_gateway)

    osx = is_osx()
    newtoken = get_token()

    if osx:
        to_file("config/tokenosx.txt", str(newtoken))
    else:
        to_file("config/tokenlinux.txt", str(newtoken))

    cmd_restart_vmware(commandline)
    cmd_run("rm -f ~/.ssh/known_hosts")


def set_working_dir(commandline, projectname):
    """
    @type commandline: VagrantArguments
    @type projectname: str
    @return: None
    """
    if commandline.workingdir is None:
        vagrantfile = os.path.join(os.getcwd(), "Vagrantfile.tpl.rb")

        if os.path.exists(vagrantfile):
            commandline.workingdir = os.getcwd()
            if os.path.basename(os.path.dirname(str(commandline.workingdir))) != projectname:
                console_warning(projectname, os.path.basename(os.path.dirname(str(commandline.workingdir))))

                raise AssertionError("projectname and dirname are different")

    if commandline.workingdir is None:
        if projectname is not None:
            commandline.workingdir = abspath(os.path.join(os.getcwd(), projectname))

    if not (commandline.workingdir is not None and os.path.exists(commandline.workingdir)):
        if not os.path.exists(commandline.workingdir):
            abort(commandline.command, commandline.workingdir + " does not exist")
        else:
            abort(commandline.command, "no workingdir set")

    os.chdir(str(commandline.workingdir))
    return commandline


def to_file(fpath, txt, mode="wt"):
    """
    @type fpath: str
    @type txt: str
    @type mode: str
    @return: None
    """
    with open(fpath, mode) as f:
        f.write(txt)


def unzip(source_filename):
    """
    @type source_filename: str
    @return: None
    """
    dest_dir = os.getcwd()
    zippath = os.path.join(dest_dir, source_filename)

    if not os.path.exists(zippath):
        console("zipfile doesn't exist", zippath, color="red")

        raise FileNotFoundError(zippath)

    with zipfile.ZipFile(zippath) as zf:
        zf.extractall(dest_dir)

    extracted_dir = os.path.join(os.path.join(os.getcwd(), dest_dir), "vckube-createproject-master")

    if os.path.exists(extracted_dir):
        for mdir in os.listdir(extracted_dir):
            shutil.move(os.path.join(extracted_dir, mdir), dest_dir)

        os.rmdir(extracted_dir)

        # os.remove(os.path.join(os.getcwd(), os.path.join(dest_dir, "master.zip")))
    else:
        console_warning(extracted_dir + " not created")

        raise FileExistsError(extracted_dir + " not created")


def write_config_from_template(commandline, ntl, vmhostosx, memory, cpus):
    """
    @type commandline: VagrantArguments
    @type ntl: str
    @type vmhostosx: bool
    @type memory: int
    @type cpus: int
    @return: None
    """
    node = open(ntl).read()
    node = node.replace("<cloud-provider>", "vagrant")

    if vmhostosx:
        masterip = "192.168.14.41"
        node = node.replace("<master-private-ip>", masterip)
        node = node.replace("<name-node>", "core1.a8.nl")
    else:
        masterip = "192.168.14.51"
        node = node.replace("<master-private-ip>", masterip)
        node = node.replace("<name-node>", "node1.a8.nl")

    if memory is None or cpus is None:
        raise RuntimeError("memory is None")

    node = node.replace("<node-memory>", str(memory))

    if cpus is not None:
        node = node.replace("<node-cpus>", str(cpus))

    info(commandline.command, "master-private-ip: " + masterip)
    config = ntl.replace(".tmpl", "")
    info(commandline.command, "writing: " + config)
    open(config, "w").write(node)


def write_new_tokens(vmhostosx):
    """
    @type vmhostosx: bool
    @return: None
    """
    token = get_token()

    def tokenpath(arch):
        """
        @type arch: str
        @return: None
        """
        cwd = os.getcwd()
        configpath = os.path.join(cwd, "config")

        if not os.path.exists(configpath):
            os.mkdir(configpath)

        path2 = os.path.join(cwd, "config/token" + arch + ".txt")
        return path2

    if vmhostosx is True:
        tposx = tokenpath("osx")
        open(tposx, "w").write(token)
        cp("./config/tokenosx.txt", "./config/token.txt")
    else:
        tlin = tokenpath("linux")
        open(tlin, "w").write(token)
        cp("./config/tokenlinux.txt", "./config/token.txt")


if __name__ == "__main__":
    main()
