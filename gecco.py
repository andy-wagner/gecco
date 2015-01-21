#!/usr/bin/env python3
#========================================================================
#GECCO - Generic Enviroment for Context-Aware Correction of Orthography
# Maarten van Gompel, Wessel Stoop, Antal van den Bosch
# Centre for Language and Speech Technology
# Radboud University Nijmegen
#
# Licensed under GPLv3
#=======================================================================


from collections import OrderedDict
from threading import Thread, Queue, Lock
import sys
import os
import socket
import socketserver
import yaml
from pynlpl.formats import folia
from ucto import Tokenizer

import argparse

UCTOSEARCHDIRS = ('/usr/local/etc/ucto','/etc/ucto/','.')




class ProcessorThread(Thread):
    def __init__(self, q, lock, loadbalancemaster, **parameters):
        self.q = q
        self.lock = lock
        self.stop = False
        self.loadbalancemaster = loadbalancemaster
        self.parameters = parameters

        self.clients = {} #each thread keeps a bunch of clients open to the servers of the various modules so we don't have to reconnect constantly (= faster)

    def run(self):
        while not self.stop:
            if not q.empty():
                module, data = q.get() #data is an instance of module.UNIT
                if module.local:
                    module.run(data, self.lock, **self.parameters)
                else:
                    server, port = module.findserver(self.loadbalancemaster)
                    if (server,port) not in self.clients:
                        self.clients[(server,port)] = module.CLIENT(host,port)
                    module.runclient( self.clients[(server,port)], data, self.lock,  **self.parameters)
                q.task_done()

    def stop(self):
        self.stop = True




class Corrector:
    def __init__(self, **settings):
        self.settings = settings
        self.verifysettings()
        self.tokenizer = Tokenizer(self.settings['ucto'])
        self.modules = OrderedDict()

        #Gather servers
        self.servers = set()
        for module in self:
            if not module.local:
                for host, port in module.settings['servers']:
                    self.servers.add( (host,port) )

        self.loadbalancemaster = LoadBalanceMaster(self.servers)

        self.units = set( [m.server for m in self] )

    def verifysettings():
        if 'config' in self.settings:
            self.settings, modules = self.parseconfig(self.settings['config'])
            for module in modules:
                self.append(module)

        if 'id' not in self.settings:
            raise Exception("No ID specified")

        if 'root' not in self.settings:
            self.root = self.settings['root'] = os.path.abspath('.')

        if not 'ucto' in self.settings:
            if 'language' in self.settings:
                for dir in UCTOSEARCHDIRS:
                    if os.path.exists(dir + "/tokconfig-" + self.settings['language']):
                        self.settings['ucto'] = dir + '/tokconfig-' + self.settings['language']
            if not 'ucto' in self.settings:
                for dir in UCTOSEARCHDIRS:
                    if os.path.exists(dir + "/tokconfig-generic"):
                        self.settings['ucto'] = dir + '/tokconfig-generic'
                if not 'ucto' in self.settings:
                    raise Exception("Ucto configuration file not specified and no default found (use setting ucto=)")
        elif not os.path.exists(self.settings['ucto']):
            raise Exception("Specified ucto configuration file not found")


        if not 'logfunction' in self.settings:
            self.settings['logfunction'] = lambda x: print("[" + self.__class__.__name__ + "] " + x,file=sys.stderr)
        self.log = self.settings['logfunction']


        if not 'threads' in self.settings:
            self.settings['threads'] = 1

        if not 'minpollinterval' in self.settings:
            self.settings['minpollinterval'] = 30 #30 sec


    def parseconfig(self,configfile):
        config = yaml.load(configfile)
        #TODO: Parse!
        return settings, modules

    def __len__(self):
        return len(self.modules)

    def _getitem__(self, id):
        return self.modules[id]

    def __iter__(self):
        for module in self.modules.values():
            yield module

    def append(self, id, module):
        assert isinstance(module, Module)
        self.modules[id] = module
        module.parent = self

    def train(self,id=None):
        return self.modules[id]




    def run(self, foliadoc, id=None, **parameters):
        if isinstance(foliadoc, str):
            #We got a filename instead of a FoLiA document, that's okay
            ext = foliadoc.split('.')[-1].lower()
            if not ext in ('xml','folia','gz','bz2'):
                #Preprocessing - Tokenize input text (plaintext) and produce FoLiA output
                self.log("Starting Tokeniser")

                inputtextfile = foliadoc

                if ext == 'txt':
                    ouputtextfile = '.'.join(inputtextfile.split('.')[:-1]) + '.folia.xml'
                else:
                    outputtextfile = inputtextfile + '.folia.xml'

                tokenizer = Tokenizer(self.settings['ucto'],xmloutput=True)
                tokenizer.process(inputtextfile, outputtextfile)

                foliadoc = outputtextfile

                self.log("Tokeniser finished")

            #good, load
            self.log("Reading FoLiA document")
            foliadoc = folia.Document(file=foliadoc)


        self.log("Initialising modules on document") #not parellel, acts on same document anyway, should be very quick
        for module in self:
            module.init(foliadoc)

        self.log("Initialising threads")


        lock = Lock()
        threads = []
        for i in range(self.settings['threads']):
            thread = ProcessorThread(queue, lock, self.loadbalancemaster, **parameters)
            thread.setDaemon(True)
            thread.start()
            threads.append(thread)


        queue = Queue() #data in queue takes the form (module, data), where data is an instance of module.UNIT (a folia document or element)

        if folia.Document in units:
            self.log("\tQueuing modules handling " + str(type(folia.Document)))

            for module in self:
                if module.UNIT is folia.Document:
                    queue.put( (module, foliadoc) )

        for unit in units:
            if unit is not folia.Document:
                self.log("\tQueuing modules handling " + str(type(unit)))
                for data in foliadoc.select(unit):
                    for module in self:
                        if module.UNIT is unit:
                            queue.put( (module, data) )


        self.log("Processing all modules....")
        queue.join()

        for thread in threads:
            thread.stop()

        self.log("Finalising modules on document") #not parellel, acts on same document anyway, should be fairly quick depending on module
        for module in self:
            module.finish(foliadoc)

        self.log("Processing all modules....")
        #Store FoLiA document
        foliadoc.save()

    def server(self):
        """Starts all servers for the current host"""

        HOST = socket.getfqdn()
        for module in self:
            if not module.local:
                for h,port in module.settings['servers']:
                    if h == host:
                        #Start this server *in a separate subprocess*
                        #TODO:
                        pass

        os.wait() #blocking
        self.log("All servers ended..")


    def moduleserver(self, module_id, host, port):
        """Start one particular module's server. This method will be launched by server() in different processes"""
        module = self.module[module_id]
        self.log("Loading module")
        module.load()
        self.log("Running server...")
        module.runserver(host,port) #blocking
        self.log("Server ended..")

    def main(self):
        #command line tool
        parser = argparse.ArgumentParser(description="", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        #parser.add_argument('--run',dest='settype',help="", action='store_const',const='somevalue')
        #parser.add_argument('-f','--dataset', type=str,help="", action='store',default="",required=False)
        #parser.add_argument('-i','--number',dest="num", type=int,help="", action='store',default="",required=False)
        #parser.add_argument('bar', nargs='+', help='bar help')
        args = parser.parse_args()
        #args.storeconst, args.dataset, args.num, args.bar
        pass


class LoadBalanceMaster: #will cache thingies
    def __init__(self, availableservers, minpollinterval):
        self.availableservers = availableservers
        self.minpollinterval = minpollinterval


    def get(self,servers):
        """Returns the server from servers with the lowest load"""
        #TODO


class LoadBalanceServer: #Reports load balance back to master
    pass


class LineByLineClient:
    """Simple communication protocol between client and server, newline-delimited"""

    def __init__(self, host, port):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connected = False

    def connect(self):
        self.socket.connect( (host,port) )
        self.connected = True

    def communicate(self, msg):
        self.send(msg)
        answer = self.receive()

    def send(self, msg):
        if not self.connected: self.connect()
        if isinstance(msg, str): msg = msg.encode('utf-8')
        if msg[-1] != b"\n": msg += b"\n"
        self.sock.sendall(msg)

    def receive(self):
        buffer = b''
        cont_recv = True
        while cont_recv:
            buffer += socket.recv(1024)
            if buffer[-1] == b"\n":
                cont_recv = False
        return str(buffer,'utf-8')

class LineByLineServerHandler(socketserver.BaseRequestHandler):
    """
    The generic RequestHandler class for our server. Instantiated once per connection to the server, invokes the module's server_handler()
    """

    def handle(self):
        # self.request is the TCP socket connected to the client, self.server is the server
        cont_recv = True
        while cont_recv:
            buffer += self.request.recv(1024)
            if buffer[-1] == b"\n":
                cont_recv = False
        msg = str(buffer,'utf-8')
        response = self.server.module.server_handler(msg)
        if isinstance(response,str):
            response = response.encode('utf-8')
        if response[-1] != b"\n": response += b"\n"
        self.request.sendall(response)

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    pass

class Module:

    UNIT = folia.Document #Specifies on type of input tbe module gets. An entire FoLiA document is the default, any smaller structure element can be assigned, such as folia.Sentence or folia.Word . More fine-grained levels usually increase efficiency.
    CLIENT = LineByLineClient
    SERVER = LineByLineServerHandler

    def __init__(self,id, **settings):
        self.id = id
        self.settings = settings
        self.verifysettings()


    def verifysettings(self):
        self.local = 'servers' in self.settings
        if 'source' in self.settings:
            if isinstance(self.settings['source'],str):
                self.sources = [ self.settings['source'] ]
            else:
                self.sources = self.settings['source']
        elif 'sources' in self.settings:
            self.sources = self.settings['sources']

        if 'model' in self.settings:
            if isinstance(self.settings['model'],str):
                self.models = [ self.settings['model'] ]
            else:
                self.models = self.settings['model']
        elif 'models' in self.settings:
            self.models = self.settings['models']


        if not 'logfunction' in self.settings:
            self.settings['logfunction'] = lambda x: print(x,file=sys.stderr) #will be rather messy when multithreaded
        self.log = self.settings['logfunction']

        #Some defaults for FoLiA processing
        if not 'set' in self.settings:
            self.settings['set'] = "https://raw.githubusercontent.com/proycon/folia/master/setdefinitions/spellingcorrection.foliaset.xml"
        if not 'class' in self.settings:
            self.settings['class'] = "nonworderror"
        if not 'annotator' in self.settings:
            self.settings['annotator'] = "Gecco-" + self.__class__.__name__

    def findserver(self, loadbalanceserver):
        """Finds a suitable server for this module"""
        if self.local:
            raise Exception("Module is local")
        elif len(self.settings['servers']) == 1:
            #Easy, there is only one
            return self.settings['servers'][0] #2-tuple (host, port)
        else:
            #TODO: Do load balancing, find least busy server
            return loadbalancemaster.get(self.settings['servers'])



    ####################### CALLBACKS ###########################


    ##### Optional callbacks invoked by the Corrector (defaults may suffice)

    def init(self, foliadoc):
        """Initialises the module on the document. This method should set all the necessary declarations if they are not already present. It will be called sequentially."""
        if 'set' in self.settings and self.settings['set']:
            if not foliadoc.declared(folia.Correction, self.settings['set']):
                foliadoc.declare(folia.Correction, self.settings['set'])
        return True

    def runserver(self, host, port):
        """Runs the server. Invoked by the Corrector on start. """
        server = ThreadedTCPServer((host, port), LineByLineServerHandler)
        # Start a thread with the server -- that thread will then start one more thread for each request
        server_thread = Thread(target=server.serve_forever)
        # Exit the server thread when the main thread terminates
        server_thread.daemon = True
        server_thread.start()


    def finish(self, foliadoc):
        """Finishes the module on the document. This method can do post-processing. It will be called sequentially."""
        return False #Nothing to finish for this module

    def train(self, **parameters):
        """This method gets invoked by the Corrector to train the model. Override it in your own model, use the input files in self.sources and for each entry create the corresponding file in self.models """
        return False #Implies there is nothing to train for this module


    ##### Callbacks invoked by the Corrector that MUST be implemented:

    def run(self, data, lock, **parameters):
        """This method gets invoked by the Corrector when it runs locally."""
        raise NotImplementedError

    def runclient(self, client, data, lock,  **parameters):
        """This method gets invoked by the Corrector when it should connect to a remote server, the client instance is passed and already available (will connect on first communication)"""
        raise NotImplementedError

    ##### Callback invoked by module's server, MUST be implemented:

    def server_handler(self, msg):
        """This methods gets called by the module's server and handles a message by the client. The return value (str) is returned to the client"""
        raise NotImplementedError


    #### Callback invoked by the module itself, MUST be implemented

    def load(self):
        """Load the requested modules from self.models, module-specific so doesn't do anything by default"""
        pass


    ######################### FOLIA EDITING ##############################
    #
    # These methods are *NOT* available to server_handler() !
    # Locks ensure that the state of the FoLiA document can't be corrupted by partial unfinished edits

    def addwordsuggestions(self, lock, word, suggestions, confidence=None  ):
        self.log("Adding correction for " + word.id + " " + word.text())

        lock.acquire()
        #Determine an ID for the next correction
        correction_id = word.generate_id(folia.Correction)

        #add the correction
        word.correct(
            suggestions=suggestion,
            id=correction_id,
            set=self.settings['set'],
            cls=self.settings['class'],
            annotator=self.settings['annotator'],
            annotatortype=folia.AnnotatorType.AUTO,
            datetime=datetime.datetime.now(),
            confidence=confidence
        )
        lock.release()



    def adderrordetection(self, lock, word):
        self.log("Adding correction for " + word.id + " " + word.text())

        lock.acquire()
        #add the correction
        word.append(
            folia.ErrorDetection(
                self.doc,
                set=self.settings['set'],
                cls=self.settings['class'],
                annotator=self.settings['annotator'],
                annotatortype='auto',
                datetime=datetime.datetime.now()
            )
        )
        lock.release()

    def splitcorrection(self, lock, word, newwords,**kwargs):
        lock.acquire()
        sentence = word.sentence()
        newwords = [ folia.Word(self.doc, generate_id_in=sentence, text=w) for w in newwords ]
        kwargs['suggest'] = True
        kwargs['datetime'] = datetime.datetime.now()
        word.split(
            *newwords,
            **kwargs
        )
        lock.release()

    def mergecorrection(self, lock, newword, originalwords, **kwargs):
        lock.acquire()
        sentence = originalwords[0].sentence()
        if not sentence:
            raise Exception("Expected sentence for " + str(repr(originalwords[0])) + ", got " + str(repr(sentence)))
        newword = folia.Word(self.doc, generate_id_in=sentence, text=newword)
        kwargs['suggest'] = True
        kwargs['datetime'] = datetime.datetime.now()
        sentence.mergewords(
            newword,
            *originalwords,
            **kwargs
        )
        lock.release()



if __name__ == '__main__':
    try:
        configfile = sys.argv[1]
    except:
        print("Syntax: gecco [configfile.yml]" ,file=sys.stderr)
    corrector = Corrector(config=configfile)
    corrector.main()

