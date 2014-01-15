#!/bin/python -tt
""" PROBLEM:

    1) mspdbsrv is in charge of serializing access to PDB databases during
    parallel builds. It is started automatically in the background by MSBUILD
    and has a timeout. It can be shared between multiple MSBUILD builds.

    2) Jenkins's ProcessTreeKiller kills all processes having the BUILD_ID that
    was set at the start of the jenkins job. This means that when job1 finishes
    it takes down mspdbsrv and job2, which connected to the same server in the
    meantime, fails.

    3) You can set BUILD_ID to something else like 'DontKillMe' and Jenkins'
    ProcessTreeKiller will miss the job; BUT:

    4) If you do this, then it's still possible for mspdbsrv to timeout in the
    middle of a build, then MSBUILD will restart mspdbsrv with the "correct"
    (i.e. undesirable) BUILD_ID and we're back to square 1.

    5) Setting up mspdbsrv with a huge timeout is not recommended as it is
    reputed to be leaky.

    SOLUTION:

    Jenkins jobs register their interest in pdbsrv by running pdbsrv_srv.py.

    The server side of pdbsrv_srv hosts mspdbsrv. While there are connected
    clients it must keep mspdbsrv running with the phony BUILD_ID.

    When mspdbsrv times out, if people are still connected, then it's instantly
    restarted in a panic! If not, then the whole shebang dies gracefully.

    If (the client side of) pdbsrv_srv.py is killed, then it deregisters with
    the server.

    USAGE:

        $ cd pdbsrv_dir/
        $ python pdbsrv_srv.py -l mylogfile_not_in_workspace.log &

    This will attempt to connect to a server at localhost, creating one if
    necessary, and run until Jenkins ProcessTreeKiller comes along.

    AUTHORS:
    
        Steve Carter sweavo@gmail.com
    
"""

import argparse
import datetime
import os
import socket
import SocketServer
import subprocess
import sys
import threading
import time


BASE_THREAD_COUNT = 2

argp = argparse.ArgumentParser( "pdbsrv_srv.py" )

argp.add_argument( '-s',
                   action='store_true',
                   default=False,
                   dest='server',
                   help='Start server' )

argp.add_argument( '-H',
                   action='store',
                   dest='HOST',
                   default='localhost',
                   help="the host to listen/speak on" )

argp.add_argument( '-P',
                   action='store',
                   dest='PORT',
                   default='50005',
                   help="the port to listen/speak on" )

argp.add_argument( '-t',
                   action='store',
                   dest='TIMEOUT',
                   default='30',
                   help="after this many minutes, quit the client" )

argp.add_argument( '-v',
                   action='store_true',
                   dest='verbose',
                   default=False,
                   help="Be talkative" )

argp.add_argument( '-l',
                   action="store",
                   dest="logfile",
                   default="pdbsrv.log",
                   help="Specify logfile location" )

argp.add_argument( '--comment',
                   action="store",
                   dest="comment",
                   default="",
                   help="Ignored, can be useful for making things appear in process explorer" )

args = argp.parse_args( )

if 'BUILD_ID' in os.environ:
    bid = os.environ[ 'BUILD_ID' ]
else:
    bid = ""

print "BUILD_ID=%s" % bid


class ThreadedTCPRequestHandler( SocketServer.BaseRequestHandler ):
    def handle( self ):
        """ Handle a connection. This function lives inside a thread and drops
            the connection at the end
        """
        old_data = None
        while True:
            response = "{} clients are connected".format( threading.activeCount( ) - BASE_THREAD_COUNT )

            try:
                data = self.request.recv( 1024 ).strip( )
            except:
                data = ""

            if args.verbose:
                print "data", data

            if data == "":
                break
            else:
                threading.current_thread.name=data

            if data == "status":
                response_lines = [ "Server Status:",
                                   "\tMSPDBSRV.EXE is PID: {}".format( pdbsrv.process.pid ),
                                   "\tClient thread count: {}".format( threading.activeCount( ) - BASE_THREAD_COUNT  ),
                                   "\tAll threads:" ]
                tlist = threading.enumerate()
                for t in tlist:
                    response_lines.append( "\t\t{}: {}".format( t.ident, t.name)  )

                response = '\n'.join( response_lines )
            if old_data != data:
                old_data = data
                print data, "is alive"

            try:
                if args.verbose:
                    print "responding"
                self.request.sendall( response )
                if args.verbose:
                    print "responded"
            except:
                break

        print "Terminate server thread for '%s'. %d remain." % (
        old_data, threading.active_count( ) - BASE_THREAD_COUNT - 1 )


class ThreadedTCPServer( SocketServer.ThreadingMixIn, SocketServer.TCPServer ):
    pass

class MsPdbSrvStarter( object ):
    """ find, and run, mspdbsrv.exe """

    def __init__( self, logHandle ):
        """ find the executable """
        self.exe = None
        self.process = None
        self.logHandle = logHandle
        locations = [ "c:/Program Files (x86)/Microsoft Visual Studio 10.0/Common7/IDE/" ]
        for l in locations:
            if os.path.isfile( l + "mspdbsrv.exe" ):
                self.exe = l + "mspdbsrv.exe"
                break
        if self.exe is None:
            raise SystemExit( "Cannot find mspdbsrv in locations %s." % repr( locations ) )

    def run( self ):
        commandline = [ self.exe, "-start" ]
        self.logHandle.write( "{} starting.\n".format( commandline ) )
        self.process = subprocess.Popen( commandline )
        self.logHandle.write( "{} started on port {}. PID={}.\n".format( commandline, args.PORT, self.process.pid ) )

    def kill( self ):
        if self.process is not None:
            self.logHandle.write( "Killing {}\n".format( self.process.pid ) )
            self.process.kill( )


if args.server:
    # try to be a server
    print "Logging to {}".format( args.logfile )
    f = open( args.logfile, "w" )
    f.write( "being a server on {}:{}\n".format( args.HOST, args.PORT ) )
    pdbsrv = MsPdbSrvStarter( f )

    server = ThreadedTCPServer( (args.HOST, int( args.PORT )), ThreadedTCPRequestHandler )
    f.write( "serving...\n" )
    threading.Thread( name="Server", target=server.serve_forever ).start( )
    f.write( "%d\n" % threading.active_count( ) )
    while threading.active_count( ) <= BASE_THREAD_COUNT:
        f.write( "waiting for connections...\n" )
        f.flush( )
        time.sleep( 1.0 )
    pdbsrv.run( )
    while threading.active_count( ) > BASE_THREAD_COUNT:
        try:
            while threading.active_count( ) > BASE_THREAD_COUNT:
                time.sleep( 1.0 )
        except KeyboardInterrupt:
            f.write( "Server Ignoring keyboard interrupt\n" )
            f.flush( )
    pdbsrv.kill( )

    server.shutdown( )
    f.write( "Server exiting.\n" )
    f.close( )

else:
    # Try to be a client
    PID = os.getpid( )

    connected = False
    while not connected:
        print "Trying to be a client"
        print "client connecting"
        try:
            s = socket.socket( socket.AF_INET, socket.SOCK_STREAM )
            s.setblocking( False )
            s.settimeout( 2.5 )
            s.connect( (args.HOST, int( args.PORT )) )
            connected = True
            # we'll live for (default 30) minutes
            start_time = datetime.datetime.now( )
            timeout_time = ( datetime.datetime.now( ) - start_time ).total_seconds( )
            TIMEOUT = float( args.TIMEOUT ) * 60.0
            while timeout_time < TIMEOUT:
                if args.verbose:
                    print "Speaking"
                s.sendall( 'PID %d BUILD_ID %s\n' % ( PID, bid ) )
                if args.verbose:
                    print "Listening"
                response = s.recv( 1024 )
                if args.verbose:
                    print "Got response."

                sys.stdout.write( "pdbsrv_srv.py: client has run for {} out of {} seconds. Server said: {}\n".format( int( timeout_time ),
                                                                                                                      TIMEOUT,
                                                                                                                      response) )
                sys.stdout.flush()

                if response is None:
                    if args.verbose:
                        print "Terminate"
                    break
                if args.verbose:
                    print "Sleeping 2 secs"
                try:
                    time.sleep( 2.0 )
                except:
                    break
                timeout_time = ( datetime.datetime.now( ) - start_time ).total_seconds( )
            if args.verbose:
                print "Client closing"
            s.close( )

        except socket.error as (no, er):
            if no == 10061:
                print "Socket error ", no, er, " -- OK, going to start a server"
                print "Starting a server on {}:{}".format( args.HOST, args.PORT )
                command_line = [ 'python', 'pdbsrv_srv.py', '-s', '-l', args.logfile, '-H', args.HOST, '-P', args.PORT ]
                if args.verbose:
                    command_line.append( '-v' )
                command_line.append( '--comment')
                command_line.append( 'Started by PID {} in cwd {}, which had BUILD_ID {}.'.format( os.getpid(), os.getcwd(), os.environ['BUILD_ID'] ))
                environment = os.environ
                environment[ 'BUILD_ID' ] = 'Independent'
                proc = subprocess.Popen( command_line, env=environment )
                print "Started."
                time.sleep( 2.0 )
            else:
                print "Socket error ", no, er, " -- BAD"
                raise
        finally:
            s.close( )
