"""
Some unit tests for SSHSession.
"""
from __future__ import print_function
import errno
import json
import os
import random
import string
import socket

import paramiko
import pytest

from jumpssh import exception, SSHSession

from . import util as tests_util


@pytest.fixture(scope="module")
def docker_env():
    my_docker_env = tests_util.DockerEnv()
    my_docker_env.start_host('image_sshd', 'gateway')
    my_docker_env.start_host('image_sshd', 'remotehost')
    my_docker_env.start_host('image_sshd', 'remotehost2')
    yield my_docker_env  # provide the fixture value
    print("teardown docker_env")
    my_docker_env.clean()


def test_unknown_host():
    with pytest.raises(exception.ConnectionError) as excinfo:
        SSHSession(host='unknown_host', username='my_user').open()
    assert type(excinfo.value.__cause__) == socket.gaierror

    with pytest.raises(exception.ConnectionError) as excinfo:
        SSHSession(host='unknown_host', username='my_user').open(retry=2)
    assert type(excinfo.value.__cause__) == socket.gaierror


def test_active_close_session(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')

    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()
    assert gateway_session.is_active()

    # open an already active session should be harmless
    gateway_session.open()
    assert gateway_session.is_active()

    remotehost_ip, remotehost_port = docker_env.get_host_ip_port('remotehost')
    remotehost_session = gateway_session.get_remote_session(host=tests_util.get_host_ip(), port=remotehost_port,
                                                            username='user1', password='password1')
    assert remotehost_session.is_active()

    # check that gateway session is well closed
    gateway_session.close()
    assert not gateway_session.is_active()
    # remote session is also automatically closed
    assert not remotehost_session.is_active()

    # closing a closed session does nothing
    gateway_session.close()

    # running command on an inactive session raise a SSHException
    with pytest.raises(exception.SSHException):
        gateway_session.run_cmd('ls')


def test_active_close_session_with_context_manager(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')

    with SSHSession(host=gateway_ip, port=gateway_port,
                    username='user1', password='password1') as gateway_session:
        assert gateway_session.is_active()

        remotehost_ip, remotehost_port = docker_env.get_host_ip_port('remotehost')
        remotehost_session = gateway_session.get_remote_session(host=tests_util.get_host_ip(), port=remotehost_port,
                                                                username='user1', password='password1')
        assert remotehost_session.is_active()

    # check that gateway session is well closed
    assert not gateway_session.is_active()

    # remote session is also automatically closed
    assert not remotehost_session.is_active()

    # try reopening same session
    gateway_session.open()

    assert gateway_session.is_active()

    assert gateway_session.get_exit_code('ls') == 0


def test_ssh_connection_error(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')

    # open first ssh session to gateway
    gateway_session1 = SSHSession(host=gateway_ip, port=gateway_port,
                                  username='user1', password='password1').open()

    # modify password from session 1
    gateway_session1.run_cmd('echo "user1:newpassword" | sudo -S chpasswd')

    # try to open 2nd session
    with pytest.raises(exception.ConnectionError) as excinfo:
        SSHSession(host=gateway_ip, port=gateway_port, username='user1', password='password1').open()
    assert type(excinfo.value.__cause__) == paramiko.ssh_exception.AuthenticationException

    # set back correct password from session 1
    gateway_session1.run_cmd('echo "user1:password1" | sudo -S chpasswd')

    # try again to open 2nd session
    gateway_session2 = SSHSession(host=gateway_ip, port=gateway_port,
                                  username='user1', password='password1').open()
    assert gateway_session2.is_active()


def test_run_cmd(docker_env, capfd):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')

    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()
    assert gateway_session.is_active()

    # basic successful command
    (exit_code, output) = gateway_session.run_cmd('hostname')
    assert exit_code == 0
    assert output.strip() == 'gateway.example.com'

    # successful list command
    gateway_session.run_cmd(['cd /etc', 'ls'])

    # wrong command
    (exit_code, output) = gateway_session.run_cmd('dummy commmand', raise_if_error=False)
    assert exit_code == 127

    with pytest.raises(exception.RunCmdError) as excinfo:
        gateway_session.run_cmd('dummy commmand')
    assert excinfo.value.exit_code == 127
    assert excinfo.value.command == 'dummy commmand'

    # wrong command type
    with pytest.raises(TypeError):
        gateway_session.run_cmd({'key': 'value'})

    # standard output is empty by default (without continuous_output flag)
    gateway_session.run_cmd('ls -lta /')
    out, err = capfd.readouterr()
    assert len(out) == 0

    # display continuous output on stdout while command is running
    gateway_session.run_cmd('ls -lta /', continuous_output=True)
    out, err = capfd.readouterr()
    assert len(out) > 0

    # run command as user2
    assert gateway_session.run_cmd('whoami', username='user2')[1].strip() == 'user2'


def get_cmd_output(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')
    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()

    assert gateway_session.get_cmd_output('hostname') == 'gateway.example.com'


def test_get_exit_code(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')

    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()
    assert gateway_session.get_exit_code('ls') == 0
    assert gateway_session.get_exit_code('dummy commmand') == 127


def test_input_data(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')

    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()

    commands = ['read -p "Requesting user input value?" my_var',
                'echo $my_var']

    # without input given, command will hang until timeout is reached
    with pytest.raises(exception.TimeoutError):
        gateway_session.run_cmd(commands, timeout=5)

    # with input given, command should run correctly and return the value entered
    assert gateway_session.get_cmd_output(commands,
                                          input_data={'Requesting user input value': 'dummy_value'}
                                          ).split()[-1] == "dummy_value"


def test_get_remote_session(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')
    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()

    remotehost_ip, remotehost_port = docker_env.get_host_ip_port('remotehost')
    remotehost_session = gateway_session.get_remote_session(host=tests_util.get_host_ip(),
                                                            port=remotehost_port,
                                                            username='user1',
                                                            password='password1')

    # run basic command on remote host
    assert remotehost_session.get_cmd_output('hostname').strip() == 'remotehost.example.com'

    # request twice the same remote session just return the existing one
    assert gateway_session.get_remote_session(host=tests_util.get_host_ip(),
                                              port=remotehost_port,
                                              username='user1',
                                              password='password1') == remotehost_session

    # request another remote session to another host while an existing one already exists
    remotehost2_ip, remotehost2_port = docker_env.get_host_ip_port('remotehost2')
    remotehost2_session = gateway_session.get_remote_session(host=tests_util.get_host_ip(),
                                                             port=remotehost2_port,
                                                             username='user1',
                                                             password='password1')
    # check that new session is active
    assert remotehost2_session.is_active()
    assert remotehost2_session.get_cmd_output('hostname').strip() == 'remotehost2.example.com'

    # check that previous session from gateway has been automatically closed
    assert not remotehost_session.is_active()


def test_handle_big_json_files(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')
    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()

    remotehost_ip, remotehost_port = docker_env.get_host_ip_port('remotehost')
    remotehost_session = gateway_session.get_remote_session(host=tests_util.get_host_ip(),
                                                            port=remotehost_port,
                                                            username='user1',
                                                            password='password1')
    # generate big json file on remotehost
    remote_path = '/tmp/dummy.json'
    dummy_json = tests_util.create_random_json(50000)
    remotehost_session.file(remote_path=remote_path, content=json.dumps(dummy_json))

    # read file from remote and check json is valid and identical to source
    dummy_json_from_remote = json.loads(remotehost_session.get_cmd_output('cat %s' % remote_path))
    assert dummy_json == dummy_json_from_remote


def test_exists(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')

    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()

    assert not gateway_session.exists('/home/user1/non_existing_file')

    gateway_session.run_cmd('touch /home/user1/existing_file')
    assert gateway_session.exists('/home/user1/existing_file')

    gateway_session.run_cmd('rm /home/user1/existing_file')
    assert not gateway_session.exists('/home/user1/existing_file')

    # create file visible only by user2
    gateway_session.run_cmd(['sudo mkdir /etc/user2_private_dir',
                             'sudo touch /etc/user2_private_dir/existing_file',
                             'sudo chown user2:user2 /etc/user2_private_dir',
                             'sudo chmod 600 /etc/user2_private_dir'])

    # check it is not visible by user1 by default
    assert not gateway_session.exists('/etc/user2_private_dir/existing_file')

    # check it is readable with root access
    assert gateway_session.exists('/etc/user2_private_dir/existing_file', use_sudo=True)

    # cleanup
    gateway_session.run_cmd('sudo rm -rf /etc/user2_private_dir')


def test_put(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')
    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()

    remotehost_ip, remotehost_port = docker_env.get_host_ip_port('remotehost')
    remotehost_session = gateway_session.get_remote_session(host=tests_util.get_host_ip(),
                                                            port=remotehost_port,
                                                            username='user1',
                                                            password='password1')
    # exception is raised when local file does not exist
    local_path = 'missing_folder/missing_path'
    with pytest.raises(IOError) as excinfo:
        remotehost_session.put(local_path=local_path, remote_path='/tmp/my_file')
    assert excinfo.value.errno == errno.ENOENT
    assert excinfo.value.strerror == "Local file '%s' does not exist" % local_path

    # create random file locally
    local_path = os.path.join(os.path.dirname(__file__), 'random_file')
    dummy_json = tests_util.create_random_json()
    with open(local_path, 'wb') as random_file:
        random_file.write(json.dumps(dummy_json).encode('utf-8'))
    try:
        # copy file on remote session
        remote_path = '/tmp/random_file'
        assert remotehost_session.exists(remote_path) is False
        remotehost_session.put(local_path=local_path, remote_path=remote_path)
        assert remotehost_session.exists(remote_path) is True

        # copy file on remote session as user2 with specific file permissions
        remote_path = '/tmp/random_file2'
        assert remotehost_session.exists(remote_path) is False
        remotehost_session.put(local_path=local_path, remote_path=remote_path, owner='user2', permissions='600')
        assert remotehost_session.exists(remote_path) is True
        assert remotehost_session.get_cmd_output(
            "ls -l %s | awk '{print $3}'" % remote_path).strip() == 'user2'
        assert remotehost_session.get_cmd_output(
            "stat -c '%a %n' " + remote_path + " | awk '{print $1}'").strip() == '600'
    finally:
        os.remove(local_path)


def test_get(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')
    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()

    remotehost_ip, remotehost_port = docker_env.get_host_ip_port('remotehost')
    remotehost_session = gateway_session.get_remote_session(host=tests_util.get_host_ip(),
                                                            port=remotehost_port,
                                                            username='user1',
                                                            password='password1')

    # create random file on remote host and ensure it is properly there
    remote_path = "remote_file"
    remotehost_session.file(remote_path=remote_path, content=json.dumps(tests_util.create_random_json()))
    assert remotehost_session.exists(remote_path)

    # download that file in local folder
    local_folder = '/tmp/'
    remotehost_session.get(remote_path=remote_path, local_path=local_folder)
    local_file_path = os.path.join(local_folder, os.path.basename(remote_path))
    assert os.path.isfile(local_file_path)
    os.remove(local_file_path)

    # download that file locally specifying local filename
    local_file_path = '/tmp/downloaded_file_' + ''.join(random.choice(string.ascii_letters) for _ in range(20))
    remotehost_session.get(remote_path=remote_path, local_path=local_file_path)
    os.remove(local_file_path)

    # get remote file from location not accessible from current user
    local_folder = '/tmp/'
    restricted_remote_path = os.path.join('/etc', remote_path)
    remotehost_session.run_cmd('sudo mv %s %s' % (remote_path, restricted_remote_path))
    remotehost_session.get(remote_path=restricted_remote_path, local_path=local_folder, use_sudo=True)
    local_file_path = os.path.join(local_folder, os.path.basename(remote_path))
    assert os.path.isfile(local_file_path)
    os.remove(local_file_path)


def test_file(docker_env):
    gateway_ip, gateway_port = docker_env.get_host_ip_port('gateway')
    gateway_session = SSHSession(host=gateway_ip, port=gateway_port,
                                 username='user1', password='password1').open()

    remotehost_ip, remotehost_port = docker_env.get_host_ip_port('remotehost')
    remotehost_session = gateway_session.get_remote_session(host=tests_util.get_host_ip(),
                                                            port=remotehost_port,
                                                            username='user1',
                                                            password='password1')
    file_content = json.dumps(tests_util.create_random_json())

    # create file in a location with root access needed should fail by default
    with pytest.raises(IOError) as excinfo:
        remotehost_session.file(remote_path='/etc/a_file', content=file_content)
    assert excinfo.value.errno == errno.EACCES
    assert excinfo.value.strerror == 'Permission denied'

    # do same command with root access
    remotehost_session.file(remote_path='/etc/a_file', content=file_content, use_sudo=True)
